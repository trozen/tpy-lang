"""
TurboPython Expression Analysis

Core expression analysis including literals, names, operators, field access, and subscripts.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, FloatType, BoolType, StrType, CharType,
    VoidType, NamedType, PtrType, ConstPtrType, OwnType, ArrayType, ListType, PendingListType,
    SpanType, TypeParamRef, TypeParamKind, ListLiteralInfo, NoneType, OptionalType,
    INT32, FLOAT, STR, CHAR, BOOL, BIGINT, NONE, is_protocol_type,
)
from ..parse import (
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBoolLiteral,
    TpyNoneLiteral, TpyName, TpyBinOp, TpyUnaryOp, TpyCall, TpyMethodCall, TpyFieldAccess,
    TpyArrayLiteral, TpyListRepeat, TpySubscript, TpyCoerce
)
from ..namespace import BindingKind
from .diagnostics import SemanticError

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .operators import OperatorResolver
    from .protocols import ProtocolChecker
    from .compatibility import TypeCompatibility
    from .calls import CallAnalyzer
    from .methods import MethodAnalyzer

from tpyc import modules as builtin_modules


class ExpressionAnalyzer:
    """Core expression analysis."""

    def __init__(
        self,
        ctx: SemanticContext,
        type_ops: TypeOperations,
        operators: OperatorResolver,
        protocols: ProtocolChecker,
        compat: TypeCompatibility,
    ):
        self.ctx = ctx
        self.type_ops = type_ops
        self.operators = operators
        self.protocols = protocols
        self.compat = compat
        # Set via set_cross_deps() to break circular dependency
        self.calls: CallAnalyzer | None = None
        self.methods: MethodAnalyzer | None = None

    def set_cross_deps(self, calls: CallAnalyzer, methods: MethodAnalyzer) -> None:
        """Wire circular dependencies (must be called before analyze_expr)."""
        self.calls = calls
        self.methods = methods

    def analyze_expr(self, expr: TpyExpr) -> TpyType:
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
        elif isinstance(expr, TpyNoneLiteral):
            typ = NONE
        elif isinstance(expr, TpyName):
            typ = self._analyze_name(expr)
        elif isinstance(expr, TpyBinOp):
            typ = self._analyze_binop(expr)
        elif isinstance(expr, TpyUnaryOp):
            typ = self._analyze_unaryop(expr)
        elif isinstance(expr, TpyCall):
            typ = self.calls.analyze_call(expr)
        elif isinstance(expr, TpyMethodCall):
            typ = self.methods.analyze_method_call(expr)
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

        self.ctx.set_expr_type(expr, typ)
        return typ

    def analyze_expr_with_hint(self, expr: TpyExpr, type_hint: TpyType | None) -> TpyType:
        """Analyze an expression with an optional type hint for inference.

        The type hint allows constructs like list() to infer their type parameters
        from context (e.g., function parameter type).
        """
        if type_hint is None:
            return self.analyze_expr(expr)

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
                    if self.ctx.current_function is None:
                        typ = ListType(elem_type)
                    else:
                        literal_id = self.ctx.literal_counter
                        self.ctx.literal_counter += 1
                        info = ListLiteralInfo(
                            literal_id=literal_id,
                            expr=expr,
                            element_type=elem_type,
                            size=0,
                            is_global=self.ctx.is_top_level,
                            has_explicit_annotation=True,
                            explicit_type=type_hint
                        )
                        self.ctx.list_literals[literal_id] = info
                        self.ctx.pending_resolutions.append(literal_id)
                        typ = PendingListType(elem_type, 0, literal_id)
                    self.ctx.set_expr_type(expr, typ)
                    return typ
                else:
                    # Other generic types (Array, etc.): use hint directly
                    # Set call_type so codegen knows the concrete template type
                    if is_generic_constructor:
                        expr.call_type = type_hint  # type: ignore
                    self.ctx.set_expr_type(expr, type_hint)
                    return type_hint

        # Fall back to regular analysis
        return self.analyze_expr(expr)

    def _analyze_name(self, expr: TpyName) -> TpyType:
        """Analyze a name reference."""
        # Check for INT type parameter references in generic class context
        # INT type params can be used as values in expressions (e.g., Int32(N))
        if self.ctx.record_ctx.type_params and self.ctx.record_ctx.type_param_kinds:
            try:
                idx = self.ctx.record_ctx.type_params.index(expr.name)
                if self.ctx.record_ctx.type_param_kinds[idx] == TypeParamKind.INT:
                    # INT type param - return TypeParamRef with INT kind
                    # This represents a compile-time constant, treated as Int32-compatible
                    return TypeParamRef(expr.name, kind=TypeParamKind.INT)
            except ValueError:
                pass  # Not a type parameter

        # Use namespace for unified lookup (includes builtins)
        if self.ctx.current_ns:
            binding = self.ctx.current_ns.lookup(expr.name)
            if binding:
                if binding.kind == BindingKind.VARIABLE:
                    self._check_definitely_assigned(expr)
                    return binding.type
                if binding.kind == BindingKind.BUILTIN:
                    return binding.type
                # For other bindings (FUNCTION, RECORD, MODULE, IMPORTED_NAME),
                # the name exists but isn't usable as a variable
                raise SemanticError(f"'{expr.name}' is not a variable")

        # Migration bridge: scope may contain names not yet in namespace
        # (e.g., during incremental namespace adoption). Remove once all
        # name registration flows go through Namespace.
        typ = self.ctx.current_scope.lookup(expr.name)
        if typ is None:
            # Check built-in names (like __name__)
            if expr.name in self.ctx.builtin_names:
                return self.ctx.builtin_names[expr.name]
            raise SemanticError(f"Undefined variable: '{expr.name}'")
        self._check_definitely_assigned(expr)
        return typ

    def _check_definitely_assigned(self, expr: TpyName) -> None:
        """Check that a local variable is definitely assigned before use."""
        if (not self.ctx.init_terminated
                and expr.name in self.ctx.var_scope_depth
                and self.ctx.var_scope_depth[expr.name] >= 1
                and expr.name not in self.ctx.definitely_assigned):
            raise self.ctx.error(
                f"variable '{expr.name}' may be used before assignment", expr)

    def _analyze_binop(self, expr: TpyBinOp) -> TpyType:
        """Analyze a binary operation."""
        left_type = self.analyze_expr(expr.left)
        right_type = self.analyze_expr(expr.right)

        # Helper to check if type is any integer type
        def is_int_type(t: TpyType) -> bool:
            return isinstance(t, (Int32Type, BigIntType, IntLiteralType))

        # Helper to check if type is any numeric type
        def is_numeric_type(t: TpyType) -> bool:
            return isinstance(t, (Int32Type, BigIntType, IntLiteralType, FloatType))

        # Identity operators (is / is not) — only valid with None
        if expr.op in ("is", "is not"):
            if isinstance(left_type, NoneType) and isinstance(right_type, OptionalType):
                return BOOL
            if isinstance(right_type, NoneType) and isinstance(left_type, OptionalType):
                return BOOL
            if isinstance(left_type, NoneType) and isinstance(right_type, NoneType):
                return BOOL
            raise SemanticError(
                f"'is' / 'is not' can only compare Optional types with None, "
                f"got {left_type} and {right_type}"
            )

        # Comparison operators return Bool
        if expr.op in ("==", "!=", "<", ">", "<=", ">="):
            if is_int_type(left_type) and is_int_type(right_type):
                return BOOL
            return BOOL

        # Membership operators (in, not in) return Bool
        if expr.op in ("in", "not in"):
            # Right side must be iterable (intrinsically or via NativeIterable protocol)
            from .list_literals import ListLiteralTracker
            tracker = ListLiteralTracker(self.ctx)
            if tracker.is_type_iterable(right_type):
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
            # Still resolve for codegen (bitwise ops need the cpp template)
            if result := self.operators.resolve_binop(left_type, expr.op, right_type):
                expr.resolved_binop = result
            return IntLiteralType(0)  # Value not tracked for compound expressions

        # Protocol-typed operands - look up the dunder method in the protocol
        # For Self in protocols, Self binds to the protocol itself when used as a value type
        if is_protocol_type(left_type):
            method_name = builtin_modules.BINOP_TO_METHOD.get(expr.op)
            if method_name:
                return_type = self.protocols.lookup_protocol_method_return(left_type, method_name, [right_type])
                if return_type is not None:
                    return return_type

        # Arithmetic/bitwise operators - use registry
        if result := self.operators.resolve_binop(left_type, expr.op, right_type):
            expr.resolved_binop = result
            return result.method.return_type

        # User-defined types (NamedType record) with dunder methods
        if isinstance(left_type, NamedType) and left_type.is_record:
            method_name = builtin_modules.BINOP_TO_METHOD.get(expr.op)
            if method_name:
                record = self.ctx.registry.get_record(left_type.name)
                if record and (method := record.get_method(method_name)):
                    # Check parameter count and type
                    if len(method.params) == 1:
                        _, param_type = method.params[0]
                        if param_type == right_type:
                            return method.return_type

        raise SemanticError(f"Invalid operand types for '{expr.op}': {left_type} and {right_type}")

    def _analyze_unaryop(self, expr: TpyUnaryOp) -> TpyType:
        """Analyze a unary operation."""
        operand_type = self.analyze_expr(expr.operand)

        # Logical not: validate operand type (Bool or numeric types only)
        if expr.op == "!":
            if isinstance(operand_type, (BoolType, Int32Type, BigIntType, FloatType, IntLiteralType)):
                return BOOL
            raise self.ctx.error(f"Invalid operand type for 'not': {operand_type} (expected Bool or numeric type)", expr)

        # FloatType supports unary negation
        if isinstance(operand_type, FloatType):
            if expr.op == "-":
                # Still resolve for codegen
                if result := self.operators.resolve_unaryop(operand_type, expr.op):
                    expr.resolved_unaryop = result
                return FLOAT

        # IntLiteralType special cases - preserve literal nature when possible
        if isinstance(operand_type, IntLiteralType):
            if expr.op == "-":
                # Still resolve for codegen (needs cpp template)
                if result := self.operators.resolve_unaryop(operand_type, expr.op):
                    expr.resolved_unaryop = result
                return IntLiteralType(-operand_type.value)
            if expr.op == "~":
                # Bitwise not on literal - treat as Int32
                # Still resolve for codegen
                if result := self.operators.resolve_unaryop(operand_type, expr.op):
                    expr.resolved_unaryop = result
                return INT32

        # Use registry for unary operators
        if result := self.operators.resolve_unaryop(operand_type, expr.op):
            expr.resolved_unaryop = result
            return result.method.return_type

        raise SemanticError(f"Invalid operand type for unary '{expr.op}': {operand_type}")

    def _analyze_field_access(self, expr: TpyFieldAccess) -> TpyType:
        """Analyze a field access."""
        # Check for module variable access (e.g., sys.argv)
        if isinstance(expr.obj, TpyName):
            if self.ctx.current_ns:
                binding = self.ctx.current_ns.lookup(expr.obj.name)
                if binding and binding.kind == BindingKind.MODULE:
                    # Get actual module name (may differ from local name for aliased imports)
                    module_name = binding.import_source[0] if binding.import_source else expr.obj.name
                    module_info = self.ctx.registry.get_module(module_name)
                    if module_info and expr.field in module_info.variables:
                        return module_info.variables[expr.field].type
                    # If not a variable, let it fall through to error at the end
                    # (method calls are handled in _analyze_method_call)

        obj_type = self.analyze_expr(expr.obj)

        # Handle pointer types - dereference to get the pointee
        # Handle Own[T] - unwrap to get the owned type
        # Handle Optional[T] - unwrap to get the inner type
        actual_type = obj_type
        if isinstance(obj_type, (PtrType, ConstPtrType)):
            actual_type = obj_type.pointee
        elif isinstance(obj_type, OwnType):
            actual_type = obj_type.wrapped
        elif isinstance(obj_type, OptionalType):
            actual_type = obj_type.inner

        if isinstance(actual_type, NamedType) and actual_type.is_record:
            record = self.ctx.registry.get_record(actual_type.name)
            if not record:
                raise SemanticError(f"Unknown record type: '{actual_type.name}'")
            # Build type substitution for generic records
            type_subst = self.type_ops.build_type_substitution(actual_type)
            # Look up field (including inherited fields)
            field_info = self.protocols.lookup_record_field(record, expr.field)
            if field_info:
                field_type = field_info.type
                if type_subst:
                    field_type = self.type_ops.substitute_type_params(field_type, type_subst)
                return field_type
            raise SemanticError(f"Record '{actual_type.name}' has no field '{expr.field}'")

        # Bounded type parameter - access field from protocol bound
        if isinstance(actual_type, TypeParamRef):
            bound = self.type_ops.get_type_param_bound(actual_type.name)
            if bound is not None and is_protocol_type(bound):
                protocol_info = self.ctx.registry.get_protocol(bound.name)
                if protocol_info:
                    for field_name, field_type in protocol_info.fields or []:
                        if field_name == expr.field:
                            # Substitute type params (Self -> T, protocol params)
                            type_subst: dict[str, TpyType] = {"Self": actual_type}
                            if protocol_info.type_params and bound.type_args:
                                type_subst.update(dict(zip(protocol_info.type_params, bound.type_args)))
                            return self.type_ops.substitute_types(field_type, type_subst)
                    raise self.ctx.error(f"Protocol '{bound.name}' has no field '{expr.field}'", expr)

        raise self.ctx.error(f"Cannot access field '{expr.field}' on type {obj_type}", expr)

    def _analyze_array_literal(self, expr: TpyArrayLiteral) -> TpyType:
        """Analyze an array literal [expr, expr, ...]

        In function-local contexts, returns a PendingListType that will be
        resolved to Array or list based on usage (mutation, parameter passing).
        In global/module context, returns ListType directly.
        """
        if not expr.elements:
            raise self.ctx.error("Empty array literal requires explicit type annotation", expr)

        # Analyze all elements first
        elem_types = [self.analyze_expr(e) for e in expr.elements]

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
        if self.ctx.current_function is None:
            return ListType(first_type)

        # Function-local context -> create PendingListType for deferred resolution
        literal_id = self.ctx.literal_counter
        self.ctx.literal_counter += 1

        info = ListLiteralInfo(
            literal_id=literal_id,
            expr=expr,
            element_type=first_type,
            size=size,
            is_global=self.ctx.is_top_level
        )
        self.ctx.list_literals[literal_id] = info
        self.ctx.pending_resolutions.append(literal_id)

        return PendingListType(first_type, size, literal_id)

    def _analyze_list_repeat(self, expr: TpyListRepeat) -> TpyType:
        """Analyze a list repetition: [elements...] * count"""
        count_type = self.analyze_expr(expr.count)

        if not isinstance(count_type, (Int32Type, BigIntType, IntLiteralType)):
            raise SemanticError(f"List repetition count must be an integer type, got {count_type}")

        # Note: Empty list repetition [] * N is collapsed to [] in the parser

        # Analyze all elements
        elem_types = [self.analyze_expr(e) for e in expr.elements]
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
        obj_type = self.analyze_expr(expr.obj)
        index_type = self.analyze_expr(expr.index)

        if not isinstance(index_type, (Int32Type, BigIntType, IntLiteralType)):
            raise SemanticError(f"Subscript index must be an integer type, got {index_type}")

        # Unwrap Optional to inner type for subscript access
        actual_type = obj_type
        if isinstance(obj_type, OptionalType):
            actual_type = obj_type.inner

        # Use get_element_type() trait for containers and strings
        elem_type = actual_type.get_element_type()
        if elem_type is not None:
            return elem_type

        # Protocol types - lookup __getitem__ return type
        if is_protocol_type(actual_type):
            return self._get_protocol_getitem_type(actual_type)

        # User records with __getitem__ method
        if isinstance(actual_type, NamedType) and actual_type.is_record:
            return self._get_record_getitem_type(actual_type)

        raise SemanticError(f"Cannot index type {obj_type}")

    def _get_protocol_getitem_type(self, protocol: NamedType) -> TpyType:
        """Get the return type of __getitem__ for a protocol type.

        For generic protocols like Sequence[T], this resolves T to the concrete type.
        """
        protocol_info = self.ctx.registry.get_protocol(protocol.name)
        if protocol_info is None:
            raise SemanticError(f"Unknown protocol: {protocol.name}")

        # Build type substitution map for generic protocols
        type_subst: dict[str, TpyType] = {}
        if protocol_info.type_params and protocol.type_args:
            type_subst = dict(zip(protocol_info.type_params, protocol.type_args))

        for method_sig in protocol_info.methods:
            if method_sig.name == "__getitem__":
                if type_subst:
                    return self.type_ops.substitute_types(method_sig.return_type, type_subst)
                return method_sig.return_type

        raise SemanticError(f"Protocol {protocol.name} does not support indexing")

    def _get_record_getitem_type(self, record_type: NamedType) -> TpyType:
        """Get the return type of __getitem__ for a user record type.

        Uses lookup_record_method to support inherited methods from parent
        classes and builtin types.
        """
        record = self.ctx.registry.get_record(record_type.name)
        if record is None:
            raise SemanticError(f"Unknown record type: {record_type.name}")

        getitem = self.protocols.lookup_record_method(record, "__getitem__")
        if getitem is None:
            raise SemanticError(f"Cannot index type {record_type}: no __getitem__ method")

        # Substitute type parameters if the record is generic
        type_subst = self.type_ops.build_type_substitution(record_type)
        if type_subst:
            return self.type_ops.substitute_type_params(getitem.return_type, type_subst)

        return getitem.return_type
