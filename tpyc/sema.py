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
from enum import Enum
from typing import Optional

from .typesys import (
    TpyType, Int32Type, VoidType, RecordType, PtrType, ConstPtrType, OwnType,
    ArrayType, SpanType, ListType, PendingListType, ListLiteralInfo, TypeParamRef,
    StrType, CharType, BoolType, BigIntType, IntLiteralType, FloatType, ProtocolType, SelfType,
    INT32, VOID, STR, CHAR, BOOL, FLOAT, BIGINT, SELF, FieldInfo, RecordInfo, FunctionInfo, TypeRegistry,
    ProtocolInfo, MethodSignature
)
from .namespace import Namespace, BindingKind, NameBinding
from .parse import (
    SourceLocation,
    TpyModule, TpyRecord, TpyFunction, TpyProtocol, TpyStmt, TpyExpr,
    TpyVarDecl, TpyAssign, TpyAugAssign, TpyExprStmt, TpyReturn, TpyIf, TpyWhile, TpyFor, TpyForEach, TpyBreak, TpyContinue,
    TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBoolLiteral, TpyName, TpyBinOp, TpyUnaryOp, TpyCall, TpyMethodCall, TpyFieldAccess,
    TpyArrayLiteral, TpyListRepeat, TpySubscript, TpyCoerce
)
from .coercions import resolve_coercion, Coercion, CoercionContext
from tpyc import modules as builtin_modules


class DiagnosticLevel(Enum):
    """Severity level of a compiler diagnostic."""
    ERROR = "error"
    WARNING = "warning"


@dataclass
class Diagnostic:
    """A compiler diagnostic (error or warning)."""
    level: DiagnosticLevel
    message: str
    loc: SourceLocation | None = None

    def format(self, filename: str = "<unknown>") -> str:
        """Format diagnostic with file:line prefix."""
        if self.loc:
            return f"{filename}:{self.loc.line}: {self.level.value}: {self.message}"
        return f"{filename}: {self.level.value}: {self.message}"


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

        # Import tracking
        # imports: module_name -> set of imported names (for "from X import Y")
        #          module_name -> None (for "import X")
        self.imports: dict[str, set[str] | None] = {}
        # imported_names: name -> (module_name, function_name) for direct function access
        self.imported_names: dict[str, tuple[str, str]] = {}

        # Built-in names (reserved for future builtins if needed)
        self.builtin_names: dict[str, TpyType] = {}

        # Unified namespace system
        # builtins_ns: root namespace containing built-in names
        # global_ns: module-level namespace (records, functions, imports, global vars)
        # current_ns: current scope during analysis (local_ns -> global_ns -> builtins_ns)
        self.builtins_ns: Namespace = Namespace()
        self.global_ns: Namespace = Namespace(parent=self.builtins_ns)
        self.current_ns: Optional[Namespace] = None

        # Track current generic class type parameters (for allowing TypeParamRef in method locals)
        self._current_record_type_params: list[str] | None = None

    def _error(self, message: str, node: TpyExpr | TpyStmt | None = None) -> SemanticError:
        """Create a SemanticError with location from a node."""
        loc = getattr(node, 'loc', None) if node else None
        return SemanticError(message, loc)

    def _warning(self, message: str, node: TpyExpr | TpyStmt | None = None) -> None:
        """Record a warning diagnostic (doesn't stop compilation)."""
        loc = getattr(node, 'loc', None) if node else None
        self.diagnostics.append(Diagnostic(DiagnosticLevel.WARNING, message, loc))

    def analyze(self, module: TpyModule) -> None:
        """Analyze a module for semantic correctness."""
        # Process imports
        self.imports = module.imports
        for module_name, names in self.imports.items():
            if names is not None:
                # "from X import Y" - register each imported name
                for name in names:
                    self.imported_names[name] = (module_name, name)
                    self.global_ns.bind_imported_name(name, module_name, name)
            else:
                # "import X" - register module name
                self.global_ns.bind_module(module_name)

        # First pass: register all records
        for record in module.records:
            self._register_record(record)

        # Register protocols
        for protocol in module.protocols:
            self._register_protocol(protocol)

        # Second pass: register all functions
        for func in module.functions:
            self._register_function(func)

        # Third pass: register top-level variable declarations (globals)
        if module.top_level_stmts:
            self._register_globals(module.top_level_stmts)

        # Register module-level __name__ (user assignments will overwrite at runtime)
        self.global_scope.define("__name__", STR)
        self.global_ns.bind_variable("__name__", STR)

        # Fourth pass: analyze top-level statements (globals must be in scope for functions)
        if module.top_level_stmts:
            self._analyze_top_level(module.top_level_stmts)

        # Fifth pass: analyze record methods
        for record in module.records:
            self._analyze_record_methods(record)

        # Sixth pass: analyze function bodies
        for func in module.functions:
            self._analyze_function(func)

    def _register_record(self, record: TpyRecord) -> None:
        """Register a record type."""
        # For generic records, skip validation of TypeParamRef types
        is_generic = bool(record.type_params)

        # Validate field types
        for fld in record.fields:
            if not self._is_type_param_ref(fld.type):
                self._validate_type(fld.type, allow_type_param_ref=is_generic)
            # Protocol types cannot be used as field types
            if isinstance(fld.type, ProtocolType):
                raise SemanticError(
                    f"Protocol type '{fld.type.name}' cannot be used as a field type in '{record.name}'. "
                    f"Protocols are only valid for function parameters"
                )

        init_params = []
        if record.init_method:
            for pname, ptype in record.init_method.params:
                # Protocol types cannot be used in __init__ parameters
                if isinstance(ptype, ProtocolType):
                    raise SemanticError(
                        f"Protocol type '{ptype.name}' cannot be used as a parameter type in '{record.name}.__init__'. "
                        f"Protocols are only valid for free function parameters"
                    )
                init_params.append((pname, ptype, None))

        # Register all methods
        methods = {}
        for method in record.methods:
            for pname, ptype in method.params:
                if not self._is_type_param_ref(ptype):
                    self._validate_type(ptype, allow_type_param_ref=is_generic)
                # Protocol types cannot be used in method parameters
                if isinstance(ptype, ProtocolType):
                    raise SemanticError(
                        f"Protocol type '{ptype.name}' cannot be used as a parameter type in '{record.name}.{method.name}'. "
                        f"Protocols are only valid for free function parameters"
                    )
            if not self._is_type_param_ref(method.return_type):
                self._validate_type(method.return_type, allow_type_param_ref=is_generic)
            # Protocol types cannot be used as method return types
            if isinstance(method.return_type, ProtocolType):
                raise SemanticError(
                    f"Protocol type '{method.return_type.name}' cannot be used as a return type in '{record.name}.{method.name}'. "
                    f"Protocols are only valid for free function parameters"
                )
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
            methods=methods,
            type_params=record.type_params
        )
        self.registry.register_record(info)
        self.global_ns.bind_record(info)

    def _register_protocol(self, protocol: TpyProtocol) -> None:
        """Register a protocol type."""
        info = ProtocolInfo(
            name=protocol.name,
            methods=protocol.methods
        )
        self.registry.register_protocol(info)

    def _register_function(self, func: TpyFunction) -> None:
        """Register a function."""
        for pname, ptype in func.params:
            self._validate_type(ptype)
            # Self type can only be used in protocol method signatures
            if isinstance(ptype, SelfType):
                raise SemanticError(
                    f"Self type cannot be used in function parameter '{pname}'. "
                    f"Self is only valid in protocol method signatures",
                    func.loc
                )
        self._validate_type(func.return_type)

        # Self type can only be used in protocol method signatures
        if isinstance(func.return_type, SelfType):
            raise SemanticError(
                f"Self type cannot be used as a return type. "
                f"Self is only valid in protocol method signatures",
                func.loc
            )

        # Protocol types cannot be used as return types
        if isinstance(func.return_type, ProtocolType):
            raise SemanticError(
                f"Protocol type '{func.return_type.name}' cannot be used as a return type. "
                f"Protocols are only valid for function parameters",
                func.loc
            )

        info = FunctionInfo(
            name=func.name,
            params=func.params,
            return_type=func.return_type,
            is_noalloc=func.is_noalloc
        )
        self.registry.register_function(info)
        self.global_ns.bind_function(info)

    def _is_type_param_ref(self, typ: TpyType) -> bool:
        """Check if a type is or contains a TypeParamRef."""
        if isinstance(typ, TypeParamRef):
            return True
        if isinstance(typ, (PtrType, ConstPtrType)):
            return self._is_type_param_ref(typ.pointee)
        if isinstance(typ, OwnType):
            return self._is_type_param_ref(typ.wrapped)
        if isinstance(typ, (ListType, SpanType, ArrayType)):
            return self._is_type_param_ref(typ.element_type)
        return False

    def _validate_type(self, typ: TpyType, allow_type_param_ref: bool = False) -> None:
        """Validate that a type is well-formed.

        Args:
            typ: The type to validate.
            allow_type_param_ref: If True, TypeParamRef is allowed (for generic class definitions).
        """
        if isinstance(typ, TypeParamRef):
            if not allow_type_param_ref:
                raise SemanticError(f"Type parameter '{typ.name}' used outside of generic class definition")
            return
        if isinstance(typ, RecordType):
            record_info = self.registry.get_record(typ.name)
            if not record_info:
                # Allow forward references during registration
                pass
            elif typ.type_args:
                # Validate type arguments for generic record
                if not record_info.is_generic():
                    raise SemanticError(f"Record '{typ.name}' is not generic, but type arguments were provided")
                if len(typ.type_args) != len(record_info.type_params):
                    raise SemanticError(
                        f"Record '{typ.name}' expects {len(record_info.type_params)} type arguments, "
                        f"got {len(typ.type_args)}"
                    )
                # Validate each type argument
                for arg in typ.type_args:
                    self._validate_type(arg, allow_type_param_ref)
            elif record_info.is_generic():
                # Generic record used without type arguments
                raise SemanticError(
                    f"Generic record '{typ.name}' requires type arguments: "
                    f"{typ.name}[{', '.join(record_info.type_params)}]"
                )
        elif isinstance(typ, (PtrType, ConstPtrType)):
            self._validate_type(typ.pointee, allow_type_param_ref)
            if isinstance(typ.pointee, ProtocolType):
                raise SemanticError(
                    f"Protocol type '{typ.pointee.name}' cannot be used as a pointer element type"
                )
        elif (elem_type := typ.get_element_type()) is not None:
            self._validate_type(elem_type, allow_type_param_ref)
            if isinstance(elem_type, ProtocolType):
                raise SemanticError(
                    f"Protocol type '{elem_type.name}' cannot be used as a container element type"
                )

    def _substitute_type_params(self, typ: TpyType, subst: dict[str, TpyType]) -> TpyType:
        """Substitute type parameters with concrete types.

        Args:
            typ: The type containing potential TypeParamRef instances.
            subst: Mapping from type parameter names to concrete types.

        Returns:
            The type with all TypeParamRef instances replaced by their concrete types.
        """
        if isinstance(typ, TypeParamRef):
            if typ.name in subst:
                return subst[typ.name]
            raise SemanticError(f"Unknown type parameter '{typ.name}'")
        # Use map_inner_types for types that have inner types
        return typ.map_inner_types(lambda t: self._substitute_type_params(t, subst))

    def _build_type_substitution(self, record_type: RecordType) -> dict[str, TpyType]:
        """Build a type parameter substitution map for a generic record instantiation.

        Args:
            record_type: A RecordType with type_args (e.g., Stack[Int32]).

        Returns:
            Mapping from type parameter names to concrete types (e.g., {"T": Int32}).
        """
        record_info = self.registry.get_record(record_type.name)
        if not record_info or not record_info.is_generic():
            return {}
        if not record_type.type_args:
            return {}
        return dict(zip(record_info.type_params, record_type.type_args))

    def _register_globals(self, stmts: list[TpyStmt]) -> None:
        """Register top-level variable declarations in global scope.

        Only registers explicitly typed globals here. Untyped globals are
        fully analyzed in _analyze_top_level, which provides proper context
        for list literal type inference.
        """
        for stmt in stmts:
            if isinstance(stmt, TpyVarDecl) and stmt.type:
                self.global_scope.define(stmt.name, stmt.type)
                self.global_ns.bind_variable(stmt.name, stmt.type)

    def _analyze_record_methods(self, record: TpyRecord) -> None:
        """Analyze all methods of a record."""
        # Track type parameters for generic records (allows TypeParamRef in method locals)
        self._current_record_type_params = record.type_params if record.type_params else None

        for method in record.methods:
            self._reset_function_tracking()
            self.current_function = method
            self.current_scope = Scope(parent=self.global_scope)
            # Add 'self' as the record type
            self.current_scope.define("self", RecordType(record.name))

            # Add parameters
            for pname, ptype in method.params:
                self.current_scope.define(pname, ptype)

            # Set up local namespace
            local_ns = Namespace(parent=self.global_ns)
            local_ns.bind_variable("self", RecordType(record.name))
            for pname, ptype in method.params:
                local_ns.bind_variable(pname, ptype)
            self.current_ns = local_ns

            # Analyze body
            for stmt in method.body:
                self._analyze_stmt(stmt)

            # Resolve pending list types after analyzing the full method
            self._resolve_pending_list_types()

            self.current_scope = None
            self.current_function = None
            self.current_ns = None

        self._current_record_type_params = None

    def _analyze_function(self, func: TpyFunction) -> None:
        """Analyze a function body."""
        self._reset_function_tracking()
        self.current_function = func
        self.current_scope = Scope(parent=self.global_scope)

        # Add parameters to scope
        for pname, ptype in func.params:
            self.current_scope.define(pname, ptype)

        # Set up local namespace
        local_ns = Namespace(parent=self.global_ns)
        for pname, ptype in func.params:
            local_ns.bind_variable(pname, ptype)
        self.current_ns = local_ns

        # Analyze body
        for stmt in func.body:
            self._analyze_stmt(stmt)

        # Resolve pending list types after analyzing the full function
        self._resolve_pending_list_types()

        self.current_function = None
        self.current_scope = None
        self.current_ns = None

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

        # Set up namespace - use global_ns for top-level (globals are visible)
        # New local variables will be added to global_ns as they're declared
        self.current_ns = self.global_ns

        for stmt in stmts:
            self._analyze_stmt(stmt)

        # Resolve pending list types (same as function analysis)
        self._resolve_pending_list_types()

        self.current_function = None
        self.current_scope = None
        self.current_ns = None
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
                                               coercion_ctx=CoercionContext.RETURN, is_return=True)
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
            # Set up inner namespace for loop variable
            old_ns = self.current_ns
            if self.current_ns:
                inner_ns = Namespace(parent=self.current_ns)
                inner_ns.bind_variable(stmt.var, INT32)
                self.current_ns = inner_ns
            self.loop_depth += 1
            for s in stmt.body:
                self._analyze_stmt(s)
            self.loop_depth -= 1
            self.current_scope = old_scope
            self.current_ns = old_ns
        elif isinstance(stmt, TpyForEach):
            iterable_type = self._analyze_expr(stmt.iterable)
            elem_type = self._get_iterable_element_type(iterable_type)
            inner_scope = Scope(self.current_scope)
            inner_scope.define(stmt.var, elem_type)
            old_scope = self.current_scope
            self.current_scope = inner_scope
            # Set up inner namespace for loop variable
            old_ns = self.current_ns
            if self.current_ns:
                inner_ns = Namespace(parent=self.current_ns)
                inner_ns.bind_variable(stmt.var, elem_type)
                self.current_ns = inner_ns
            self.loop_depth += 1
            for s in stmt.body:
                self._analyze_stmt(s)
            self.loop_depth -= 1
            self.current_scope = old_scope
            self.current_ns = old_ns
        elif isinstance(stmt, TpyBreak):
            if self.loop_depth == 0:
                raise SemanticError("'break' outside loop")
        elif isinstance(stmt, TpyContinue):
            if self.loop_depth == 0:
                raise SemanticError("'continue' outside loop")

    def _analyze_var_decl(self, stmt: TpyVarDecl) -> None:
        """Analyze a variable declaration."""
        # Validate the type annotation if present
        # Allow TypeParamRef when inside a generic record's methods
        if stmt.type:
            try:
                self._validate_type(stmt.type, allow_type_param_ref=bool(self._current_record_type_params))
            except SemanticError as e:
                raise self._error(str(e), stmt)

        # Own[T] is only valid for function parameters and return types, not variables
        if stmt.type and isinstance(stmt.type, OwnType):
            raise self._error(
                f"Own[{stmt.type.wrapped}] cannot be used as a variable type. "
                f"Use '{stmt.type.wrapped}' instead (Own[T] is for parameters and return types only)",
                stmt
            )

        # Protocol types can only be used for function parameters, not variables
        if stmt.type and isinstance(stmt.type, ProtocolType):
            raise self._error(
                f"Protocol type '{stmt.type.name}' cannot be used as a variable type. "
                f"Protocols are only valid for function parameters",
                stmt
            )

        # Check if this is a reassignment (variable already exists in scope)
        existing_type = self.current_scope.lookup(stmt.name)

        if stmt.init:
            # Handle empty list literal or generic type constructor with explicit type annotation
            # Note: [] * N is collapsed to [] in the parser
            is_empty_literal = isinstance(stmt.init, TpyArrayLiteral) and not stmt.init.elements
            is_generic_constructor = (isinstance(stmt.init, TpyCall) and
                                      not stmt.init.args and
                                      stmt.init.call_type is None and
                                      builtin_modules.lookup_generic_type(stmt.init.func) is not None)

            if (is_empty_literal or is_generic_constructor) and stmt.type:
                # Check if annotation matches the constructor's generic type
                annotation_matches = False
                if is_generic_constructor:
                    lookup = builtin_modules.lookup_generic_type(stmt.init.func)
                    annotation_matches = (lookup is not None and
                                          stmt.type.qualified_name() == lookup.qualified_name)
                else:
                    # Empty literal [] can match list[T] annotation
                    annotation_matches = isinstance(stmt.type, ListType)

                if annotation_matches:
                    if isinstance(stmt.type, ListType):
                        # list[T]: Use PendingListType for potential Array optimization
                        elem_type = stmt.type.element_type
                        # Set call_type so codegen generates explicit type (e.g., std::vector<int>())
                        if is_generic_constructor:
                            stmt.init.call_type = stmt.type  # type: ignore
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
                        # Other generic types (Array, etc.): use annotation directly
                        init_type = stmt.type
                        # Set call_type so codegen knows the concrete template type
                        if is_generic_constructor:
                            stmt.init.call_type = stmt.type  # type: ignore
                    # Cache expr_type since we bypassed _analyze_expr
                    self.expr_types[id(stmt.init)] = init_type
                else:
                    func_name = stmt.init.func if is_generic_constructor else "[]"
                    raise SemanticError(
                        f"{func_name} requires matching type annotation, got {stmt.type}"
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
                                                  coercion_ctx=CoercionContext.INIT)
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
                                                  coercion_ctx=CoercionContext.ASSIGN)
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
        if self.current_ns:
            self.current_ns.bind_variable(stmt.name, var_type)
        # Track var_decl for later type updates
        if isinstance(var_type, IntLiteralType):
            self.var_decl_by_name[stmt.name] = stmt

    def _analyze_assign(self, stmt: TpyAssign) -> None:
        """Analyze an assignment."""
        target_type = self._analyze_expr(stmt.target)
        value_type = self._analyze_expr(stmt.value)

        # Prevent assignment to read-only types via MutableSequence protocol check
        if isinstance(stmt.target, TpySubscript):
            obj_type = self.get_expr_type(stmt.target.obj)
            elem_type = obj_type.get_element_type()
            if elem_type is not None:
                # Check if type conforms to MutableSequence[elem_type]
                mutable_seq = ProtocolType("MutableSequence", (elem_type,))
                if not self._type_conforms_to_protocol(obj_type, mutable_seq):
                    raise self._error(f"Cannot assign to elements of {obj_type} (read-only)", stmt)

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
                if self.current_ns:
                    self.current_ns.update_variable_type(stmt.target.name, value_type)
                self.expr_types[id(stmt.target)] = value_type
                # Update var_types so codegen knows the resolved type
                var_decl = self.var_decl_by_name.get(stmt.target.name)
                if var_decl:
                    self.var_types[id(var_decl)] = value_type
                return

        stmt.value = self._coerce_expr(stmt.value, value_type, target_type, "assignment",
                                       coercion_ctx=CoercionContext.ASSIGN)

    def _analyze_aug_assign(self, stmt: TpyAugAssign) -> None:
        """Analyze an augmented assignment (+=, -=, etc.)."""
        target_type = self._analyze_expr(stmt.target)
        value_type = self._analyze_expr(stmt.value)
        # Both must be numeric types for arithmetic augmented assignment
        if not isinstance(target_type, (Int32Type, BigIntType, IntLiteralType, FloatType)):
            raise SemanticError(f"Augmented assignment target must be a numeric type, got {target_type}")
        if not isinstance(value_type, (Int32Type, BigIntType, IntLiteralType, FloatType)):
            raise SemanticError(f"Augmented assignment value must be a numeric type, got {value_type}")

    def _analyze_expr_with_hint(self, expr: TpyExpr, type_hint: Optional[TpyType]) -> TpyType:
        """Analyze an expression with an optional type hint for inference.

        The type hint allows constructs like list() to infer their type parameters
        from context (e.g., function parameter type).
        """
        if type_hint is None:
            return self._analyze_expr(expr)

        # Check for generic type constructor (list(), Container[T](), etc.)
        is_generic_constructor = (isinstance(expr, TpyCall) and
                                  not expr.args and
                                  expr.call_type is None and
                                  builtin_modules.lookup_generic_type(expr.func) is not None)

        # Check for empty list literal []
        is_empty_literal = isinstance(expr, TpyArrayLiteral) and not expr.elements

        if is_generic_constructor or is_empty_literal:
            # Check if type_hint matches the constructor's generic type
            hint_matches = False
            if is_generic_constructor:
                lookup = builtin_modules.lookup_generic_type(expr.func)  # type: ignore
                hint_matches = (lookup is not None and
                                type_hint.qualified_name() == lookup.qualified_name)
            else:
                # Empty literal [] can match list[T] hint
                hint_matches = isinstance(type_hint, ListType)

            if hint_matches:
                if isinstance(type_hint, ListType):
                    # list[T]: Use PendingListType for potential Array optimization
                    elem_type = type_hint.element_type
                    # Set call_type so codegen generates explicit type (e.g., std::vector<int>())
                    if is_generic_constructor:
                        expr.call_type = type_hint  # type: ignore
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
                else:
                    # Other generic types (Array, etc.): use hint directly
                    # Set call_type so codegen knows the concrete template type
                    if is_generic_constructor:
                        expr.call_type = type_hint  # type: ignore
                    self.expr_types[id(expr)] = type_hint
                    return type_hint

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
        elif isinstance(expr, TpyFloatLiteral):
            typ = FLOAT
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
        # Use namespace for unified lookup (includes builtins)
        if self.current_ns:
            binding = self.current_ns.lookup(expr.name)
            if binding:
                if binding.kind == BindingKind.VARIABLE:
                    return binding.type
                if binding.kind == BindingKind.BUILTIN:
                    return binding.type
                # For other bindings (FUNCTION, RECORD, MODULE, IMPORTED_NAME),
                # the name exists but isn't usable as a variable
                raise SemanticError(f"'{expr.name}' is not a variable")

        # Fallback to old scope lookup for compatibility
        typ = self.current_scope.lookup(expr.name)
        if typ is None:
            # Check built-in names (like __name__)
            if expr.name in self.builtin_names:
                return self.builtin_names[expr.name]
            raise SemanticError(f"Undefined variable: '{expr.name}'")
        return typ

    def _analyze_binop(self, expr: TpyBinOp) -> TpyType:
        """Analyze a binary operation."""
        left_type = self._analyze_expr(expr.left)
        right_type = self._analyze_expr(expr.right)

        # Helper to check if type is any integer type
        def is_int_type(t: TpyType) -> bool:
            return isinstance(t, (Int32Type, BigIntType, IntLiteralType))

        # Helper to check if type is any numeric type
        def is_numeric_type(t: TpyType) -> bool:
            return isinstance(t, (Int32Type, BigIntType, IntLiteralType, FloatType))

        # Comparison operators return Bool
        if expr.op in ("==", "!=", "<", ">", "<=", ">="):
            if is_int_type(left_type) and is_int_type(right_type):
                return BOOL
            return BOOL

        # Membership operators (in, not in) return Bool
        if expr.op in ("in", "not in"):
            # Right side must be iterable (intrinsically or via NativeIterable protocol)
            if self._is_type_iterable(right_type):
                # For string containers, LHS must be str or Char
                if isinstance(right_type, StrType):
                    if not isinstance(left_type, (StrType, CharType)):
                        raise SemanticError(
                            f"Cannot check '{left_type}' membership in str (expected str or Char)",
                            expr.loc
                        )
                return BOOL
            raise SemanticError(f"Cannot use '{expr.op}' with non-iterable type {right_type}")

        # Logical operators return Bool
        if expr.op in ("&&", "||"):
            return BOOL

        # IntLiteral + IntLiteral -> IntLiteral (stays unresolved until context determines type)
        if isinstance(left_type, IntLiteralType) and isinstance(right_type, IntLiteralType):
            return IntLiteralType(0)  # Value not tracked for compound expressions

        # Protocol-typed operands - look up the dunder method in the protocol
        # For Self in protocols, Self binds to the protocol itself when used as a value type
        if isinstance(left_type, ProtocolType):
            method_name = builtin_modules.BINOP_TO_METHOD.get(expr.op)
            if method_name:
                return_type = self._lookup_protocol_method_return(left_type, method_name, [right_type])
                if return_type is not None:
                    return return_type

        # Arithmetic/bitwise operators - use module system
        if result := builtin_modules.lookup_binop(left_type, expr.op, right_type):
            return result.method.returns

        # User-defined types (RecordType) with dunder methods
        if isinstance(left_type, RecordType):
            method_name = builtin_modules.BINOP_TO_METHOD.get(expr.op)
            if method_name:
                record = self.registry.get_record(left_type.name)
                if record and (method := record.get_method(method_name)):
                    # Check parameter count and type
                    if len(method.params) == 1:
                        _, param_type = method.params[0]
                        if param_type == right_type:
                            return method.return_type

        raise SemanticError(f"Invalid operand types for '{expr.op}': {left_type} and {right_type}")

    def _analyze_unaryop(self, expr: TpyUnaryOp) -> TpyType:
        """Analyze a unary operation."""
        operand_type = self._analyze_expr(expr.operand)

        # Logical not always returns Bool
        if expr.op == "!":
            return BOOL

        # FloatType supports unary negation
        if isinstance(operand_type, FloatType):
            if expr.op == "-":
                return FLOAT

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
        # Generic type instantiation (e.g., Container[T, N]())
        if expr.call_type is not None:
            for arg in expr.args:
                self._analyze_expr(arg)
            return expr.call_type

        # Check module registry for built-in functions (global builtins like chr)
        if builtin_fn := builtin_modules.lookup_function(expr.func):
            return self._analyze_builtin_call(expr, builtin_fn)

        # Use namespace for unified lookup - handles shadowing automatically
        if self.current_ns:
            binding = self.current_ns.lookup(expr.func)
            if binding:
                if binding.kind == BindingKind.VARIABLE:
                    raise SemanticError(f"'{expr.func}' is not callable")
                elif binding.kind == BindingKind.FUNCTION:
                    return self._analyze_user_function_call(expr, binding.func_info)
                elif binding.kind == BindingKind.RECORD:
                    return self._analyze_record_constructor(expr, binding.record_info)
                elif binding.kind == BindingKind.IMPORTED_NAME:
                    module_name, func_name = binding.import_source
                    if imported_fn := builtin_modules.lookup_module_function(module_name, func_name):
                        return self._analyze_builtin_call(expr, imported_fn)
                    raise SemanticError(f"Unknown function '{func_name}' in module '{module_name}'")
                elif binding.kind == BindingKind.MODULE:
                    raise SemanticError(f"Cannot call module '{expr.func}' directly; use module.function()")
                elif binding.kind == BindingKind.BUILTIN:
                    raise SemanticError(f"'{expr.func}' is not callable")

        # Fallback to old lookup for compatibility (when current_ns not set)
        # Check if it's an imported function (from X import Y)
        # Only if not shadowed by a variable, user-defined function, or record
        if expr.func in self.imported_names:
            is_shadowed = (self.current_scope.lookup(expr.func) is not None or
                           self.registry.get_function(expr.func) is not None or
                           self.registry.get_record(expr.func) is not None)
            if not is_shadowed:
                module_name, func_name = self.imported_names[expr.func]
                if imported_fn := builtin_modules.lookup_module_function(module_name, func_name):
                    return self._analyze_builtin_call(expr, imported_fn)

        # Built-in print()
        if expr.func == "print":
            for arg in expr.args:
                self._analyze_expr(arg)
            return VOID

        # Check if it's a builtin type constructor (e.g., Int32, int)
        if type_def := builtin_modules.lookup_type_by_func_name(expr.func):
            return self._check_constructor(expr, type_def)

        # Fallback: Check if it's a record constructor
        record = self.registry.get_record(expr.func)
        if record:
            # Analyze arguments
            for arg in expr.args:
                self._analyze_expr(arg)
            return RecordType(expr.func)

        # Fallback: Check if it's a function call
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
                                                    coercion_ctx=CoercionContext.ARG)
                    expr.args[i] = coerced_arg

                # Track parameter context for list inference
                if isinstance(arg_type, PendingListType):
                    self._mark_list_param_context(arg, ptype)

            return func.return_type

        # Generic type constructor without context for type inference
        if lookup := builtin_modules.lookup_generic_type(expr.func):
            type_def = lookup.type_def
            params = ", ".join(type_def.type_params)

            # Check for constructors that can infer type from arguments
            if expr.args and type_def.constructors:
                arg_types = [self._analyze_expr(arg) for arg in expr.args]
                for ctor in type_def.constructors:
                    if len(ctor.params) != len(arg_types):
                        continue
                    # Try to match and infer type parameters
                    inferred_params = self._match_generic_constructor(ctor.params, arg_types)
                    if inferred_params is not None:
                        # Use type_factory to create the result type
                        elem_type = inferred_params.get("T")
                        if elem_type and type_def.type_factory:
                            # Resolve IntLiteralType to BigInt (Python semantics)
                            if isinstance(elem_type, IntLiteralType):
                                elem_type = BIGINT
                            result_type = type_def.type_factory(elem_type)
                            expr.call_type = result_type
                            return result_type

            if expr.args:
                raise self._error(
                    f"Cannot infer element type for {expr.func}() from these arguments; "
                    f"use {expr.func}[{params}]() or provide a type annotation",
                    expr
                )
            raise self._error(
                f"Cannot infer element type for {expr.func}(); "
                f"use {expr.func}[{params}](), provide a type annotation, or pass an iterable",
                expr
            )

        raise SemanticError(f"Unknown function or type: '{expr.func}'")

    def _analyze_builtin_call(self, expr: TpyCall, fn_def: builtin_modules.BuiltinFunctionDef) -> TpyType:
        """Type-check a call to a built-in function from the module registry."""
        arg_types = [self._analyze_expr(arg) for arg in expr.args]

        for overload in fn_def.overloads:
            if len(overload.params) != len(arg_types):
                continue
            if all(self._builtin_type_matches(arg_t, param.type)
                   for arg_t, param in zip(arg_types, overload.params)):
                # Apply coercions to arguments where needed
                for i, (arg, arg_t, param) in enumerate(zip(expr.args, arg_types, overload.params)):
                    if arg_t != param.type:
                        expr.args[i] = self._coerce_expr(arg, arg_t, param.type,
                                                         f"argument '{param.name}'",
                                                         coercion_ctx=CoercionContext.ARG)
                return overload.returns

        # No matching overload found - build error message
        arg_type_strs = ", ".join(str(t) for t in arg_types)
        raise SemanticError(f"No matching overload for {fn_def.name}({arg_type_strs})")

    def _builtin_type_matches(self, arg_type: TpyType, param_type: TpyType) -> bool:
        """Check if an argument type is compatible with a builtin parameter type."""
        if arg_type == param_type:
            return True
        # Protocol parameter: check if arg_type conforms to the protocol
        if isinstance(param_type, ProtocolType):
            return self._type_conforms_to_protocol(arg_type, param_type)
        # Check if there's a coercion from arg_type to param_type
        if resolve_coercion(arg_type, param_type, CoercionContext.ARG) is not None:
            return True
        return False

    def _analyze_user_function_call(self, expr: TpyCall, func: FunctionInfo) -> TpyType:
        """Analyze a call to a user-defined function."""
        if len(expr.args) != len(func.params):
            raise SemanticError(f"Function '{expr.func}' expects {len(func.params)} arguments, got {len(expr.args)}")
        for i, ((pname, ptype), arg) in enumerate(zip(func.params, expr.args)):
            # Handle list() constructor - infer type from parameter
            arg_type = self._analyze_expr_with_hint(arg, ptype)

            # Check for Own[T] passed directly to object type parameter
            if isinstance(arg_type, OwnType) and not isinstance(ptype, OwnType) and not ptype.is_value_type():
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
                                                coercion_ctx=CoercionContext.ARG)
                expr.args[i] = coerced_arg

            # Track parameter context for list inference
            if isinstance(arg_type, PendingListType):
                self._mark_list_param_context(arg, ptype)

        return func.return_type

    def _analyze_record_constructor(self, expr: TpyCall, record: RecordInfo) -> TpyType:
        """Analyze a call to a record constructor."""
        # Check if this is a generic record instantiation (e.g., Stack[Int32]())
        if expr.call_type is not None and isinstance(expr.call_type, RecordType):
            # Validate type arguments
            if record.is_generic():
                if not expr.call_type.type_args:
                    raise self._error(
                        f"Generic record '{record.name}' requires type arguments: "
                        f"{record.name}[{', '.join(record.type_params)}]",
                        expr
                    )
                if len(expr.call_type.type_args) != len(record.type_params):
                    raise self._error(
                        f"Record '{record.name}' expects {len(record.type_params)} type arguments, "
                        f"got {len(expr.call_type.type_args)}",
                        expr
                    )
            # Analyze and type-check constructor arguments with type substitution
            type_subst = self._build_type_substitution(expr.call_type)
            if record.has_init:
                # Type-check __init__ parameters
                if len(expr.args) != len(record.init_params):
                    raise self._error(
                        f"{record.name}() takes {len(record.init_params)} argument(s), got {len(expr.args)}",
                        expr
                    )
                for i, (arg, (pname, ptype, _)) in enumerate(zip(expr.args, record.init_params)):
                    arg_type = self._analyze_expr(arg)
                    resolved_ptype = self._substitute_type_params(ptype, type_subst) if type_subst else ptype
                    expr.args[i] = self._coerce_expr(arg, arg_type, resolved_ptype, f"argument '{pname}'",
                                                     coercion_ctx=CoercionContext.ARG)
            else:
                for arg in expr.args:
                    self._analyze_expr(arg)
            return expr.call_type
        # Non-generic record
        if record.is_generic():
            raise self._error(
                f"Generic record '{record.name}' requires type arguments: "
                f"{record.name}[{', '.join(record.type_params)}]",
                expr
            )
        for arg in expr.args:
            self._analyze_expr(arg)
        return RecordType(record.name)

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
            # param.type is TpyType after resolve_method (including ProtocolType)
            param_type: TpyType = param.type  # type: ignore
            expr.args[i] = self._coerce_expr(arg, arg_type, param_type, f"{param.name} argument",
                                             coercion_ctx=CoercionContext.ARG)
        # method.returns is TpyType after resolve_method (not TypeParam)
        return_type: TpyType = method.returns  # type: ignore
        return return_type

    def _analyze_method_call(self, expr: TpyMethodCall) -> TpyType:
        """Analyze a method call."""
        # Check for module.function() pattern (import X -> X.func())
        # Use namespace to check if module binding exists and isn't shadowed
        if isinstance(expr.obj, TpyName):
            if self.current_ns:
                binding = self.current_ns.lookup(expr.obj.name)
                if binding and binding.kind == BindingKind.MODULE:
                    # It's a module call: module.function()
                    module_name = expr.obj.name
                    if module_fn := builtin_modules.lookup_module_function(module_name, expr.method):
                        temp_call = TpyCall(func=expr.method, args=expr.args, loc=expr.loc)
                        return self._analyze_builtin_call(temp_call, module_fn)
                    raise SemanticError(f"Module '{module_name}' has no function '{expr.method}'")
            # Fallback for when namespace isn't set
            elif expr.obj.name in self.imports:
                module_name = expr.obj.name
                # Check if shadowed by variable, user-defined function, or record
                if (self.current_scope.lookup(module_name) is None and
                    self.registry.get_function(module_name) is None and
                    self.registry.get_record(module_name) is None):
                    # Module was imported with 'import X' (not 'from X import ...')
                    if self.imports[module_name] is None:
                        if module_fn := builtin_modules.lookup_module_function(module_name, expr.method):
                            # Create a temporary TpyCall to analyze the function call
                            temp_call = TpyCall(func=expr.method, args=expr.args, loc=expr.loc)
                            return self._analyze_builtin_call(temp_call, module_fn)
                        raise SemanticError(f"Module '{module_name}' has no function '{expr.method}'")

        obj_type = self._analyze_expr(expr.obj)

        # List mutation methods - mark literal as mutated before module lookup
        if isinstance(obj_type, (PendingListType, ListType)):
            mutation_methods = {"append", "pop", "insert", "remove", "clear", "extend", "__setitem__"}
            if expr.method in mutation_methods:
                self._mark_list_mutated(expr.obj)

        # Built-in type methods - use module lookup (works for any type with qualified_name)
        type_params = builtin_modules.extract_type_params(obj_type)
        methods = builtin_modules.lookup_type_method(obj_type, expr.method)
        if methods:
            resolved = builtin_modules.resolve_method(methods[0], type_params)
            return self._check_method_args(expr, resolved, obj_type)

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
                # Build type substitution for generic records
                type_subst = self._build_type_substitution(obj_type)
                # Type-check arguments (with type parameter substitution)
                for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, method_info.params)):
                    arg_type = self._analyze_expr(arg)
                    # Substitute type parameters in parameter type
                    resolved_ptype = self._substitute_type_params(ptype, type_subst) if type_subst else ptype
                    expr.args[i] = self._coerce_expr(arg, arg_type, resolved_ptype, f"argument '{pname}'",
                                                     coercion_ctx=CoercionContext.ARG)
                # Substitute type parameters in return type
                return_type = method_info.return_type
                if type_subst:
                    return_type = self._substitute_type_params(return_type, type_subst)
                return return_type

        # Protocol-typed values - use protocol method signatures
        if isinstance(obj_type, ProtocolType):
            method_sig = self._get_protocol_method_signature(obj_type, expr.method)
            if method_sig is None:
                raise SemanticError(f"Protocol '{obj_type.name}' has no method '{expr.method}'")

            params, return_type = method_sig
            # Check argument count
            if len(expr.args) != len(params):
                raise SemanticError(
                    f"Method '{expr.method}' expects {len(params)} arguments, "
                    f"got {len(expr.args)}"
                )
            # Type-check and coerce arguments
            for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, params)):
                arg_type = self._analyze_expr(arg)
                expr.args[i] = self._coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                 coercion_ctx=CoercionContext.ARG)
            return return_type

        raise SemanticError(f"Cannot call method '{expr.method}' on type {obj_type}")

    def _analyze_field_access(self, expr: TpyFieldAccess) -> TpyType:
        """Analyze a field access."""
        # Check for module variable access (e.g., sys.argv)
        if isinstance(expr.obj, TpyName):
            if self.current_ns:
                binding = self.current_ns.lookup(expr.obj.name)
                if binding and binding.kind == BindingKind.MODULE:
                    module_name = expr.obj.name
                    if module_var := builtin_modules.lookup_module_var(module_name, expr.field):
                        return module_var.type
                    # If not a variable, let it fall through to error at the end
                    # (method calls are handled in _analyze_method_call)

        obj_type = self._analyze_expr(expr.obj)

        # Handle pointer types - dereference to get the pointee
        # Handle Own[T] - unwrap to get the owned type
        actual_type = obj_type
        if isinstance(obj_type, (PtrType, ConstPtrType)):
            actual_type = obj_type.pointee
        elif isinstance(obj_type, OwnType):
            actual_type = obj_type.wrapped

        if isinstance(actual_type, RecordType):
            record = self.registry.get_record(actual_type.name)
            if not record:
                raise SemanticError(f"Unknown record type: '{actual_type.name}'")
            # Build type substitution for generic records
            type_subst = self._build_type_substitution(actual_type)
            for fld in record.fields:
                if fld.name == expr.field:
                    # Substitute type parameters in field type
                    field_type = fld.type
                    if type_subst:
                        field_type = self._substitute_type_params(field_type, type_subst)
                    return field_type
            raise SemanticError(f"Record '{actual_type.name}' has no field '{expr.field}'")

        raise SemanticError(f"Cannot access field '{expr.field}' on type {obj_type}")

    def _analyze_array_literal(self, expr: TpyArrayLiteral) -> TpyType:
        """Analyze an array literal [expr, expr, ...]

        In function-local contexts, returns a PendingListType that will be
        resolved to Array or list based on usage (mutation, parameter passing).
        In global/module context, returns ListType directly.
        """
        if not expr.elements:
            raise self._error("Empty array literal requires explicit type annotation", expr)

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
        """Analyze a list repetition: [elements...] * count"""
        count_type = self._analyze_expr(expr.count)

        if not isinstance(count_type, (Int32Type, BigIntType, IntLiteralType)):
            raise SemanticError(f"List repetition count must be an integer type, got {count_type}")

        # Note: Empty list repetition [] * N is collapsed to [] in the parser

        # Analyze all elements
        elem_types = [self._analyze_expr(e) for e in expr.elements]
        first_type = elem_types[0]

        # Check all elements are compatible (similar to array literal)
        for i, elem_type in enumerate(elem_types[1:], 2):
            if isinstance(first_type, IntLiteralType) and isinstance(elem_type, IntLiteralType):
                continue
            if isinstance(elem_type, IntLiteralType) and isinstance(first_type, (Int32Type, BigIntType)):
                continue
            if isinstance(first_type, IntLiteralType) and isinstance(elem_type, (Int32Type, BigIntType)):
                first_type = elem_type
                continue
            if first_type != elem_type:
                raise SemanticError(f"List repetition element {i} has type {elem_type}, expected {first_type}")

        # Keep IntLiteralType so it can coerce to annotated type (list[Int32] or list[int])
        return ListType(first_type)

    def _analyze_subscript(self, expr: TpySubscript) -> TpyType:
        """Analyze subscript indexing: obj[index]"""
        obj_type = self._analyze_expr(expr.obj)
        index_type = self._analyze_expr(expr.index)

        if not isinstance(index_type, (Int32Type, BigIntType, IntLiteralType)):
            raise SemanticError(f"Subscript index must be an integer type, got {index_type}")

        # Use get_element_type() trait for containers and strings
        elem_type = obj_type.get_element_type()
        if elem_type is not None:
            return elem_type

        # Protocol types - lookup __getitem__ return type
        if isinstance(obj_type, ProtocolType):
            return self._get_protocol_getitem_type(obj_type)

        # User records with __getitem__ method
        if isinstance(obj_type, RecordType):
            return self._get_record_getitem_type(obj_type)

        raise SemanticError(f"Cannot index type {obj_type}")

    def _get_protocol_getitem_type(self, protocol: ProtocolType) -> TpyType:
        """Get the return type of __getitem__ for a protocol type.

        For generic protocols like Sequence[T], this resolves T to the concrete type.
        """
        protocol_def = builtin_modules.lookup_protocol(protocol.name)
        if protocol_def is not None:
            getitem_method = protocol_def.methods.get("__getitem__")
            if getitem_method is None:
                raise SemanticError(f"Protocol {protocol.name} does not support indexing")

            # Build type substitution map for generic protocols
            if protocol_def.type_params and protocol.type_args:
                type_subst = dict(zip(protocol_def.type_params, protocol.type_args))
                resolved = builtin_modules.resolve_method(getitem_method, type_subst)
                return resolved.returns
            return getitem_method.returns

        # User-defined protocol - check in registry
        protocol_info = self.registry.get_protocol(protocol.name)
        if protocol_info is None:
            raise SemanticError(f"Unknown protocol: {protocol.name}")

        for method_sig in protocol_info.methods:
            if method_sig.name == "__getitem__":
                return method_sig.return_type

        raise SemanticError(f"Protocol {protocol.name} does not support indexing")

    def _get_record_getitem_type(self, record_type: RecordType) -> TpyType:
        """Get the return type of __getitem__ for a user record type."""
        record = self.registry.get_record(record_type.name)
        if record is None:
            raise SemanticError(f"Unknown record type: {record_type.name}")

        getitem = record.get_method("__getitem__")
        if getitem is None:
            raise SemanticError(f"Cannot index type {record_type}: no __getitem__ method")

        return getitem.return_type

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
            # Generic type constructor creates a temporary
            if expr.call_type is not None:
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
            # 'self' in a method is safe - refers to the receiver object
            # (its lifetime is managed by the caller)
            if expr.name == "self":
                return False

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

    def _match_generic_constructor(
        self, params: list[builtin_modules.ParamDef], arg_types: list[TpyType]
    ) -> dict[str, TpyType] | None:
        """Try to match constructor params against arg types and infer type parameters.

        Returns dict of inferred type params (e.g., {"T": Int32}) on success, None on failure.
        Supports protocol params like "NativeIterable[T]" which infer T from element type.
        """
        import re
        inferred: dict[str, TpyType] = {}
        for param, arg_type in zip(params, arg_types):
            if isinstance(param.type, str):
                # Check for Protocol[TypeParam] pattern (e.g., "NativeIterable[T]")
                protocol_match = re.match(r"(\w+)\[(\w+)\]", param.type)
                if protocol_match:
                    protocol_name = protocol_match.group(1)
                    type_param = protocol_match.group(2)
                    # Get element type from argument (via get_element_type)
                    elem_type = self._get_iterable_element_type_or_none(arg_type)
                    if elem_type is None:
                        return None  # Arg doesn't conform to iterable protocol
                    # Check if arg conforms to the protocol with inferred element type
                    expected_protocol = ProtocolType(protocol_name, [elem_type])
                    if not self._type_conforms_to_protocol(arg_type, expected_protocol):
                        return None
                    # Check consistency with previously inferred type param
                    if type_param in inferred and inferred[type_param] != elem_type:
                        return None
                    inferred[type_param] = elem_type
                elif param.type in inferred:
                    # Simple type param like "T" - check consistency
                    if not builtin_modules._type_matches_param(arg_type, inferred[param.type]):
                        return None
                else:
                    # Unknown string pattern
                    return None
            elif isinstance(param.type, TpyType):
                # Concrete type - must match exactly
                if not builtin_modules._type_matches_param(arg_type, param.type):
                    return None
            else:
                # Unknown param type pattern
                return None
        return inferred

    def _get_element_type(self, typ: TpyType) -> TpyType | None:
        """Extract element type from an iterable type, or None if not iterable."""
        return typ.get_element_type()

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

    def _substitute_self(self, typ: TpyType, actual: TpyType) -> TpyType:
        """Recursively substitute SelfType with actual type throughout a type structure.

        Handles nested types like Own[Self], Ptr[Self], list[Self], etc.
        Uses map_inner_types for generic traversal of wrapper types.
        """
        if isinstance(typ, SelfType):
            return actual
        return typ.map_inner_types(lambda t: self._substitute_self(t, actual))

    def _get_protocol_method_signature(
        self,
        protocol: ProtocolType,
        method_name: str,
    ) -> tuple[list[tuple[str, TpyType]], TpyType] | None:
        """Get a method's signature from a protocol.

        Returns (params, return_type) with Self substituted, or None if not found.
        """
        # Check builtin protocols first
        protocol_def = builtin_modules.lookup_protocol(protocol.name)
        if protocol_def is not None:
            method_def = protocol_def.methods.get(method_name)
            if method_def is None:
                return None

            # Build type substitution: Self -> the protocol type itself
            type_subst: dict[str, TpyType] = {"Self": protocol}
            if protocol_def.type_params and protocol.type_args:
                type_subst.update(dict(zip(protocol_def.type_params, protocol.type_args)))

            resolved = builtin_modules.resolve_method(method_def, type_subst)
            params = [(p.name, p.type) for p in resolved.params]
            return (params, resolved.returns)

        # Check user-defined protocols
        protocol_info = self.registry.get_protocol(protocol.name)
        if protocol_info is not None:
            for method_sig in protocol_info.methods:
                if method_sig.name == method_name:
                    # Substitute Self -> protocol in params and return type
                    params = [
                        (pname, self._substitute_self(ptype, protocol))
                        for pname, ptype in method_sig.params
                    ]
                    return_type = self._substitute_self(method_sig.return_type, protocol)
                    return (params, return_type)

        return None

    def _lookup_protocol_method_return(
        self,
        protocol: ProtocolType,
        method_name: str,
        arg_types: list[TpyType],
    ) -> TpyType | None:
        """Look up a method's return type in a protocol, checking argument compatibility.

        For protocol-typed values, we use the protocol's method signatures.
        Self in the protocol is bound to the protocol itself (not a concrete type).

        Returns the method's return type if found and args match, None otherwise.
        """
        # Check builtin protocols first
        protocol_def = builtin_modules.lookup_protocol(protocol.name)
        if protocol_def is not None:
            method_def = protocol_def.methods.get(method_name)
            if method_def is None:
                return None

            # Build type substitution: Self -> the protocol type itself
            type_subst: dict[str, TpyType] = {"Self": protocol}
            if protocol_def.type_params and protocol.type_args:
                type_subst.update(dict(zip(protocol_def.type_params, protocol.type_args)))

            resolved = builtin_modules.resolve_method(method_def, type_subst)

            # Check argument count
            if len(resolved.params) != len(arg_types):
                return None

            # Check argument types
            for param_def, arg_type in zip(resolved.params, arg_types):
                if param_def.type != arg_type:
                    return None

            return resolved.returns

        # Check user-defined protocols
        protocol_info = self.registry.get_protocol(protocol.name)
        if protocol_info is not None:
            for method_sig in protocol_info.methods:
                if method_sig.name == method_name:
                    # Check argument count
                    if len(method_sig.params) != len(arg_types):
                        return None

                    # Check argument types (recursively substituting Self -> protocol)
                    for (_, ptype), arg_type in zip(method_sig.params, arg_types):
                        expected_type = self._substitute_self(ptype, protocol)
                        if expected_type != arg_type:
                            return None

                    # Return type (recursively substituting Self -> protocol)
                    return self._substitute_self(method_sig.return_type, protocol)

        return None

    def _types_compatible_for_protocol(self, actual_return: TpyType, expected_return: TpyType) -> bool:
        """Check if actual return type is compatible with expected return type for protocol conformance.

        Allows coercions like IntLiteralType -> Int32, which enables
        PendingList[IntLiteral] to match NativeIterable[Int32].
        """
        if actual_return == expected_return:
            return True
        # Allow IntLiteralType to match any integer type it can coerce to
        if resolve_coercion(actual_return, expected_return, CoercionContext.RETURN) is not None:
            return True
        return False

    def _type_has_method_with_signature(
        self,
        actual: TpyType,
        method_name: str,
        expected_params: list[TpyType],
        expected_return: TpyType,
    ) -> bool:
        """Check if a type has a method with the expected signature.

        Works for user records (via RecordInfo), protocol types, and builtin types (via module system).

        Note: For builtin types with generic methods (e.g., list.append(value: T)), the type
        parameter comparison uses direct equality, which doesn't resolve type variables.
        This is fine for Phase 1 protocols (only Sized with __len__() -> Int32), but would
        need type parameter resolution for generic protocols like Iterable[T].
        """
        if isinstance(actual, ProtocolType):
            # Protocol type - check methods in protocol definition
            # First check builtin protocols
            protocol_def = builtin_modules.lookup_protocol(actual.name)
            if protocol_def is not None:
                method_def = protocol_def.methods.get(method_name)
                if method_def is None:
                    return False
                if method_def.returns != expected_return:
                    return False
                if len(method_def.params) != len(expected_params):
                    return False
                for param_def, expected_ptype in zip(method_def.params, expected_params):
                    if param_def.type != expected_ptype:
                        return False
                return True
            # Check user-defined protocols
            protocol_info = self.registry.get_protocol(actual.name)
            if protocol_info is not None:
                for method_sig in protocol_info.methods:
                    if method_sig.name == method_name:
                        if method_sig.return_type != expected_return:
                            return False
                        if len(method_sig.params) != len(expected_params):
                            return False
                        for (_, actual_ptype), expected_ptype in zip(method_sig.params, expected_params):
                            if actual_ptype != expected_ptype:
                                return False
                        return True
            return False
        elif isinstance(actual, RecordType):
            # User record - check methods in RecordInfo
            record = self.registry.get_record(actual.name)
            if record is None:
                return False
            method = record.get_method(method_name)
            if method is None:
                return False
            # Check return type
            if method.return_type != expected_return:
                return False
            # Check parameter count and types
            if len(method.params) != len(expected_params):
                return False
            for (_, actual_ptype), expected_ptype in zip(method.params, expected_params):
                if actual_ptype != expected_ptype:
                    return False
            return True
        else:
            # Builtin type - check via module system
            overloads = builtin_modules.lookup_type_method(actual, method_name)
            if overloads is None:
                return False

            # Extract type parameters from the actual type (e.g., {"T": Int32} for list[Int32])
            type_params = builtin_modules.extract_type_params(actual)

            # Check if any overload matches the expected signature
            for method_def in overloads:
                # Resolve method signature with type parameters from actual type
                if type_params:
                    resolved = builtin_modules.resolve_method(method_def, type_params)
                else:
                    resolved = method_def

                # Check return type (allow coercions like IntLiteral -> Int32)
                if not self._types_compatible_for_protocol(resolved.returns, expected_return):
                    continue
                # Check parameter count and types
                if len(resolved.params) != len(expected_params):
                    continue
                params_match = True
                for param_def, expected_ptype in zip(resolved.params, expected_params):
                    if not self._types_compatible_for_protocol(param_def.type, expected_ptype):
                        params_match = False
                        break
                if params_match:
                    return True
            return False

    def _type_conforms_to_protocol(self, actual: TpyType, protocol: ProtocolType) -> bool:
        """Check if actual type conforms to a protocol.

        Two conformance mechanisms work in parallel:
        1. Explicit extends: type declares extends=["Protocol[T]"]
        2. Structural: type has all methods required by the protocol

        For marker protocols (no methods), only explicit extends works.
        For protocols with methods, either mechanism suffices.

        For generic protocols like Sequence[Int32]:
        - Build type substitution map: {"T": Int32}
        - Resolve each method signature with substitutions
        - Check if actual type has the resolved methods

        For Self type in protocols:
        - Self is substituted with the actual type being checked
        - e.g., checking Int32 against Addable with __add__(Self) -> Self
          expects __add__(Int32) -> Int32
        """
        # Look up the protocol definition (builtin first, then user-defined)
        protocol_def = builtin_modules.lookup_protocol(protocol.name)
        if protocol_def is not None:
            # Protocol-to-protocol: check type args match
            if isinstance(actual, ProtocolType) and actual.name == protocol.name:
                return actual.type_args == protocol.type_args

            # Check explicit extends declaration
            if builtin_modules.type_extends_protocol(actual, protocol.name, protocol.type_args):
                return True

            # Marker protocols (no methods) require explicit extends
            if not protocol_def.methods:
                return False

            # Build type substitution map for generic protocols
            # Always include Self -> actual type
            type_subst: dict[str, TpyType] = {"Self": actual}
            if protocol_def.type_params and protocol.type_args:
                if len(protocol_def.type_params) != len(protocol.type_args):
                    return False  # Mismatch in type parameter count
                type_subst.update(dict(zip(protocol_def.type_params, protocol.type_args)))

            # Structural conformance: check all protocol methods
            for method_name, method_def in protocol_def.methods.items():
                # Resolve method signature with type substitutions (including Self)
                resolved = builtin_modules.resolve_method(method_def, type_subst)
                expected_params = [p.type for p in resolved.params]
                expected_return = resolved.returns

                if not self._type_has_method_with_signature(
                    actual, method_name, expected_params, expected_return
                ):
                    return False
            return True

        # User-defined protocol - check in registry
        protocol_info = self.registry.get_protocol(protocol.name)
        if protocol_info is None:
            return False

        # Build type substitution map for generic user protocols
        type_subst: dict[str, TpyType] = {"Self": actual}
        if protocol_info.type_params and protocol.type_args:
            if len(protocol_info.type_params) != len(protocol.type_args):
                return False
            type_subst.update(dict(zip(protocol_info.type_params, protocol.type_args)))

        for method_sig in protocol_info.methods:
            # Recursively substitute SelfType with actual type in params and return type
            # Handles nested types like Own[Self], Ptr[Self], etc.
            expected_params = [
                self._substitute_self(ptype, actual)
                for _, ptype in method_sig.params
            ]
            expected_return = self._substitute_self(method_sig.return_type, actual)

            if not self._type_has_method_with_signature(
                actual, method_sig.name, expected_params, expected_return
            ):
                return False
        return True

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

        # Protocol matching (structural subtyping)
        if isinstance(expected, ProtocolType):
            if self._type_conforms_to_protocol(actual, expected):
                return None  # No coercion needed, structural match
            raise SemanticError(
                f"Type {actual} does not conform to protocol {expected} in {context}",
                loc
            )

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

        # Allow ListType -> ArrayType only for literal expressions
        # (global array literals and list repeats become ListType but can be assigned to Array variables)
        # List *variables* cannot be coerced to Array - codegen can't handle std::vector -> std::array
        if isinstance(actual, ListType) and isinstance(expected, ArrayType):
            if isinstance(source_expr, (TpyArrayLiteral, TpyListRepeat)):
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

    def _is_type_iterable(self, typ: TpyType) -> bool:
        """Check if a type is iterable (extends NativeIterable or is NativeIterable protocol).

        A type is iterable if it declares extends=["NativeIterable[T]"] or is itself
        a NativeIterable[T] protocol type.
        """
        # NativeIterable[T] protocol type
        if isinstance(typ, ProtocolType) and typ.name == "NativeIterable":
            return True
        # Check if type extends NativeIterable
        return builtin_modules.type_extends_any(typ, "NativeIterable")

    def _get_iterable_element_type_or_none(self, iterable_type: TpyType) -> TpyType | None:
        """Get the element type of an iterable, or None if not iterable.

        For types extending NativeIterable[T], returns T.
        """
        # Handle NativeIterable[T] protocol type
        if isinstance(iterable_type, ProtocolType) and iterable_type.name == "NativeIterable":
            if iterable_type.type_args:
                return iterable_type.type_args[0]
            return None

        # Handle str -> Char
        if isinstance(iterable_type, StrType):
            return CHAR

        # Use get_element_type() for container types (list, Array, Span, etc.)
        return iterable_type.get_element_type()

    def _get_iterable_element_type(self, iterable_type: TpyType) -> TpyType:
        """Get the element type of an iterable for for-each loops.

        Raises SemanticError if type is not iterable.
        """
        elem_type = self._get_iterable_element_type_or_none(iterable_type)
        if elem_type is not None:
            return elem_type
        raise SemanticError(f"Cannot iterate over type {iterable_type}")
