"""
TurboPython Expression Analysis

Core expression analysis including literals, names, operators, field access, and subscripts.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, FloatType, BoolType, StrType, CharType,
    NamedType, PtrType, ConstPtrType, OwnType, ListType, PendingListType,
    TypeParamRef, TypeParamKind, ListLiteralInfo, NoneType, OptionalType, UnionType,
    ReadonlyType, unwrap_readonly,
    INT32, FLOAT, STR, CHAR, BOOL, BIGINT, NONE, is_protocol_type,
)
from ..parse import (
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBoolLiteral,
    TpyNoneLiteral, TpyName, TpyBinOp, TpyUnaryOp, TpyCall, TpyMethodCall, TpyFieldAccess,
    TpyArrayLiteral, TpyListRepeat, TpySubscript, TpyCoerce
)
from ..namespace import BindingKind
from .diagnostics import SemanticError, OPTIONAL_NONE_ACCESS_WARNING
from .narrowing import NarrowingTracker

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
        narrowing: NarrowingTracker,
    ):
        self.ctx = ctx
        self.type_ops = type_ops
        self.operators = operators
        self.protocols = protocols
        self.compat = compat
        self.narrowing = narrowing
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
            raise self.ctx.error(f"Unknown expression type: {type(expr).__name__}", expr)

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
            # Unwrap ReadonlyType/OwnType so hints like Own[list[T]] work
            inner_hint = unwrap_readonly(type_hint)
            if isinstance(inner_hint, OwnType):
                inner_hint = inner_hint.wrapped
            # Check if type_hint matches the constructor's generic type
            hint_matches = False
            if is_generic_constructor:
                lookup = builtin_modules.lookup_generic_type(expr.func)  # type: ignore
                hint_matches = (lookup is not None and
                                inner_hint.qualified_name() == lookup.qualified_name)
            else:
                # Empty literal [] can match list[T] hint
                hint_matches = isinstance(inner_hint, ListType)

            if hint_matches:
                if isinstance(inner_hint, ListType):
                    # list[T]: Use PendingListType for potential Array optimization
                    elem_type = inner_hint.element_type
                    # Set call_type so codegen generates explicit type (e.g., std::vector<int>())
                    if is_generic_constructor:
                        expr.call_type = inner_hint  # type: ignore
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
                            explicit_type=inner_hint
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
                        expr.call_type = inner_hint  # type: ignore
                    self.ctx.set_expr_type(expr, inner_hint)
                    return inner_hint

        # Non-empty array literal with list type hint
        # (e.g. return [x, y] with -> Own[list[T]], or x: list[Int32|None] = [1, None])
        if isinstance(expr, TpyArrayLiteral) and expr.elements:
            inner_hint = unwrap_readonly(type_hint)
            if isinstance(inner_hint, OwnType):
                inner_hint = inner_hint.wrapped
            if isinstance(inner_hint, ListType):
                result = self._analyze_array_literal(expr, inner_hint.element_type)
                if isinstance(result, PendingListType):
                    info = self.ctx.list_literals.get(result.literal_id)
                    if info:
                        info.has_explicit_annotation = True
                        info.explicit_type = inner_hint
                        if info.coerced_element_type is None:
                            info.coerced_element_type = inner_hint.element_type
                self.ctx.set_expr_type(expr, result)
                return result

        # Fall back to regular analysis, propagating hint through context
        # for functions that need it (e.g. unsafe_cast)
        old_hint = self.ctx.expr_type_hint
        self.ctx.expr_type_hint = type_hint
        try:
            return self.analyze_expr(expr)
        finally:
            self.ctx.expr_type_hint = old_hint

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
                    return self.narrowing.narrow_name_type(expr.name, binding.type)
                if binding.kind == BindingKind.BUILTIN:
                    return binding.type
                # For other bindings (FUNCTION, RECORD, MODULE, IMPORTED_NAME),
                # the name exists but isn't usable as a variable
                raise self.ctx.error(f"'{expr.name}' is not a variable", expr)

        # Migration bridge: scope may contain names not yet in namespace
        # (e.g., during incremental namespace adoption). Remove once all
        # name registration flows go through Namespace.
        typ = self.ctx.current_scope.lookup(expr.name)
        if typ is None:
            # Check built-in names (like __name__)
            if expr.name in self.ctx.builtin_names:
                return self.ctx.builtin_names[expr.name]
            raise self.ctx.error(f"Undefined variable: '{expr.name}'", expr)
        self._check_definitely_assigned(expr)
        return self.narrowing.narrow_name_type(expr.name, typ)

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
        if expr.op in ("&&", "||"):
            type_true, type_false = self.narrowing.condition_type_facts(expr.left)
            saved_types = dict(self.ctx.narrowed_types)
            if expr.op == "&&":
                self.ctx.narrowed_types.update(type_true)
            else:
                self.ctx.narrowed_types.update(type_false)
            try:
                right_type = self.analyze_expr(expr.right)
            finally:
                self.ctx.narrowed_types = saved_types
        else:
            right_type = self.analyze_expr(expr.right)

        # Preserve declared Optional/Union type for identity checks when flow
        # narrowing resolved an expression to its inner type.
        if expr.op in ("is", "is not"):
            declared_left = self.narrowing.declared_type_for_expr(expr.left)
            if isinstance(declared_left, (OptionalType, UnionType)):
                left_type = declared_left
            declared_right = self.narrowing.declared_type_for_expr(expr.right)
            if isinstance(declared_right, (OptionalType, UnionType)):
                right_type = declared_right

        # Enforce Pythonic None identity checks for Optional values.
        # `x == None` / `x != None` on Optional values should use `is` / `is not`.
        if expr.op in ("==", "!="):
            left_is_none = isinstance(left_type, NoneType)
            right_is_none = isinstance(right_type, NoneType)
            if left_is_none or right_is_none:
                other = right_type if left_is_none else left_type
                if isinstance(other, OptionalType):
                    raise self.ctx.error(
                        "Use 'is None' / 'is not None' for Optional None checks "
                        "(not '==' / '!=')",
                        expr,
                    )
                if isinstance(other, (PtrType, ConstPtrType)):
                    raise self.ctx.error(
                        "Use 'is None' / 'is not None' for pointer None checks "
                        "(not '==' / '!=')",
                        expr,
                    )
                if isinstance(other, UnionType) and other.has_none_member():
                    raise self.ctx.error(
                        "Use 'is None' / 'is not None' for union None checks "
                        "(not '==' / '!=')",
                        expr,
                    )
                raise self.ctx.error(
                    f"Cannot compare {left_type} and {right_type} with '{expr.op}'",
                    expr,
                )

        # Value optionals in operator expressions use runtime null checks unless
        left_effective = left_type
        right_effective = right_type
        # flow already proved non-None for the specific expression.
        warned_optional_operator = False
        if expr.op not in ("is", "is not", "&&", "||", "in", "not in", "==", "!="):
            if isinstance(left_effective, OptionalType) and left_effective.inner.is_value_type():
                left_effective = left_effective.inner
                warned_optional_operator = True
            if isinstance(right_effective, OptionalType) and right_effective.inner.is_value_type():
                right_effective = right_effective.inner
                warned_optional_operator = True
            if warned_optional_operator:
                self.ctx.warning(OPTIONAL_NONE_ACCESS_WARNING, expr)

        # For ==/!=, unwrap Optional value-types only for operator resolution
        # (no warning -- C++ std::optional handles None comparison natively)
        if expr.op in ("==", "!="):
            if isinstance(left_effective, OptionalType) and left_effective.inner.is_value_type():
                left_effective = left_effective.inner
                expr.optional_safe_eq = True
            if isinstance(right_effective, OptionalType) and right_effective.inner.is_value_type():
                right_effective = right_effective.inner
                expr.optional_safe_eq = True

        # Helper to check if type is any numeric type
        def is_numeric_type(t: TpyType) -> bool:
            return isinstance(t, (Int32Type, BigIntType, IntLiteralType, FloatType))

        # Identity operators (is / is not) -- only valid with None
        if expr.op in ("is", "is not"):
            # Unwrap ReadonlyType for nullable checks.
            left_check = unwrap_readonly(left_type)
            right_check = unwrap_readonly(right_type)
            nullable_types = (OptionalType, PtrType, ConstPtrType)
            if isinstance(left_check, NoneType) and isinstance(right_check, nullable_types):
                return BOOL
            if isinstance(right_check, NoneType) and isinstance(left_check, nullable_types):
                return BOOL
            if isinstance(left_check, NoneType) and isinstance(right_check, NoneType):
                return BOOL
            # Nullable unions: v is None / v is not None
            if isinstance(right_check, NoneType) and isinstance(left_check, UnionType) and left_check.has_none_member():
                return BOOL
            if isinstance(left_check, NoneType) and isinstance(right_check, UnionType) and right_check.has_none_member():
                return BOOL
            raise self.ctx.error(
                f"'is' / 'is not' can only compare Optional/Ptr/union types with None, "
                f"got {left_type} and {right_type}",
                expr,
            )

        # Comparison operators return Bool
        if expr.op in ("==", "!=", "<", ">", "<=", ">="):
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
            raise self.ctx.error(f"Cannot use '{expr.op}' with non-iterable type {right_type}", expr)

        # Logical operators return Bool
        if expr.op in ("&&", "||"):
            return BOOL

        # IntLiteral + IntLiteral -> IntLiteral (stays unresolved until context determines type)
        if isinstance(left_effective, IntLiteralType) and isinstance(right_effective, IntLiteralType):
            # Keep Python-style true division semantics for all-literal integer
            # expressions regardless of default-int setting.
            if expr.op == "div":
                if result := self.operators.resolve_binop(BIGINT, expr.op, BIGINT):
                    expr.resolved_binop = result
                    return result.method.return_type
                return FLOAT
            literal_result = self._try_eval_int_literal_binop(expr.op, left_effective.value, right_effective.value)
            resolved_int = (
                self.ctx.default_int_for_literal(IntLiteralType(literal_result))
                if literal_result is not None
                else self.ctx.default_int_type
            )
            # Still resolve for codegen (bitwise ops need cpp template).
            if result := self.operators.resolve_binop(resolved_int, expr.op, resolved_int):
                expr.resolved_binop = result
            return IntLiteralType(literal_result)

        # Protocol-typed operands - look up the dunder method in the protocol
        # For Self in protocols, Self binds to the protocol itself when used as a value type
        if is_protocol_type(left_effective):
            method_name = builtin_modules.BINOP_TO_METHOD.get(expr.op)
            if method_name:
                return_type = self.protocols.lookup_protocol_method_return(
                    left_effective,
                    method_name,
                    [right_effective],
                )
                if return_type is not None:
                    return return_type

        # Arithmetic/bitwise operators - use registry
        if result := self.operators.resolve_binop(left_effective, expr.op, right_effective):
            expr.resolved_binop = result
            return result.method.return_type

        # User-defined types (NamedType record) with dunder methods
        if isinstance(left_effective, NamedType) and left_effective.is_user_record:
            method_name = builtin_modules.BINOP_TO_METHOD.get(expr.op)
            if method_name:
                record = self.ctx.registry.get_record(left_effective.name)
                if record and (method := record.get_method(method_name)):
                    # Check parameter count and type
                    if len(method.params) == 1:
                        _, param_type = method.params[0]
                        if param_type == right_effective:
                            return method.return_type

        raise SemanticError(
            f"Invalid operand types for '{expr.op}': {left_type} and {right_type}",
            expr.loc,
        )

    def _try_eval_int_literal_binop(self, op: str, left: int | None, right: int | None) -> int | None:
        """Best-effort constant evaluation for int literal binops."""
        if left is None or right is None:
            return None
        try:
            if op == "+":
                return left + right
            if op == "-":
                return left - right
            if op == "*":
                return left * right
            if op == "//":
                if right == 0:
                    return None
                return left // right
            if op == "%":
                if right == 0:
                    return None
                return left % right
            if op == "**":
                if right < 0 or right > 10000:
                    return None
                return left ** right
            if op == "<<":
                if right < 0 or right > 10000:
                    return None
                return left << right
            if op == ">>":
                if right < 0:
                    return None
                return left >> right
            if op == "&":
                return left & right
            if op == "|":
                return left | right
            if op == "^":
                return left ^ right
        except (OverflowError, ValueError):
            return None
        return None

    def _analyze_unaryop(self, expr: TpyUnaryOp) -> TpyType:
        """Analyze a unary operation."""
        operand_type = self.analyze_expr(expr.operand)
        effective_type = operand_type

        # Value optionals in unary arithmetic/bitwise ops use runtime checks
        # unless flow already narrowed them to non-Optional.
        if expr.op in ("-", "~") and isinstance(operand_type, OptionalType) and operand_type.inner.is_value_type():
            effective_type = operand_type.inner
            self.ctx.warning(OPTIONAL_NONE_ACCESS_WARNING, expr)

        # Logical not: validate operand type (Bool, numeric, Optional, or types with __bool__/__len__)
        if expr.op == "!":
            if isinstance(effective_type, (BoolType, Int32Type, BigIntType, FloatType, IntLiteralType, OptionalType)):
                return BOOL
            record = self.ctx.registry.get_record_for_type(effective_type)
            if record and (record.get_method_overloads("__bool__")
                           or record.get_method_overloads("__len__")):
                return BOOL
            raise self.ctx.error(f"Invalid operand type for 'not': {operand_type} (expected bool, numeric, or type with __bool__/__len__)", expr)

        # FloatType supports unary negation
        if isinstance(effective_type, FloatType):
            if expr.op == "-":
                # Still resolve for codegen
                if result := self.operators.resolve_unaryop(effective_type, expr.op):
                    expr.resolved_unaryop = result
                return FLOAT

        # IntLiteralType special cases - preserve literal nature when possible
        if isinstance(effective_type, IntLiteralType):
            if expr.op == "-":
                # Still resolve for codegen (needs cpp template)
                if result := self.operators.resolve_unaryop(effective_type, expr.op):
                    expr.resolved_unaryop = result
                neg = -effective_type.value if effective_type.value is not None else None
                return IntLiteralType(neg)
            if expr.op == "~":
                # Bitwise not on literal - treat as Int32
                # Still resolve for codegen
                if result := self.operators.resolve_unaryop(effective_type, expr.op):
                    expr.resolved_unaryop = result
                return INT32

        # Use registry for unary operators
        if result := self.operators.resolve_unaryop(effective_type, expr.op):
            expr.resolved_unaryop = result
            return result.method.return_type

        raise self.ctx.error(f"Invalid operand type for unary '{expr.op}': {operand_type}", expr)

    def get_deref_target_type(self, typ: TpyType) -> TpyType | None:
        """If typ has __deref__(), return resolved return type. Else None."""
        return self.type_ops.get_deref_target_type(typ)

    def _try_find_field(self, typ: TpyType, expr: TpyFieldAccess) -> TpyType | None:
        """Try to find a field on typ. Returns field type or None."""
        if isinstance(typ, NamedType) and typ.is_user_record:
            record = self.ctx.registry.get_record(typ.name)
            if not record:
                return None
            type_subst = self.type_ops.build_type_substitution(typ)
            field_info = self.protocols.lookup_record_field(record, expr.field)
            if field_info:
                field_type = field_info.type
                if type_subst:
                    field_type = self.type_ops.substitute_type_params(field_type, type_subst)
                return field_type
            return None

        if isinstance(typ, TypeParamRef):
            bound = self.type_ops.get_type_param_bound(typ.name)
            if bound is not None and is_protocol_type(bound):
                protocol_info = self.ctx.registry.get_protocol(bound.name)
                if protocol_info:
                    for field_name, field_type in protocol_info.fields or []:
                        if field_name == expr.field:
                            type_subst: dict[str, TpyType] = {"Self": typ}
                            if protocol_info.type_params and bound.type_args:
                                type_subst.update(dict(zip(protocol_info.type_params, bound.type_args)))
                            resolved = self.type_ops.substitute_types(field_type, type_subst)
                            return resolved
                    raise self.ctx.error(f"Protocol '{bound.name}' has no field '{expr.field}'", expr)

        return None

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

        # Unwrap transparent wrappers
        is_readonly_obj = isinstance(obj_type, ReadonlyType)
        actual_type = obj_type
        if isinstance(actual_type, ReadonlyType):
            actual_type = actual_type.wrapped
        if isinstance(actual_type, OwnType):
            actual_type = actual_type.wrapped
        elif isinstance(actual_type, OptionalType):
            if actual_type.inner.is_value_type():
                raise self.ctx.error(f"Cannot access field '{expr.field}' on type {obj_type}", expr)
            self.ctx.warning(OPTIONAL_NONE_ACCESS_WARNING, expr)
            expr.needs_optional_runtime_check = True
            actual_type = actual_type.inner

        # Deref chain loop -- resolves through Ptr, ConstPtr, and any Deref[T] type
        current_type = actual_type
        deref_depth = 0
        while deref_depth <= 8:
            result = self._try_find_field(current_type, expr)
            if result is not None:
                expr.deref_depth = deref_depth
                if (deref_depth > 0
                        and isinstance(actual_type, (PtrType, ConstPtrType))
                        and isinstance(expr.obj, TpyName)
                        and expr.obj.name in self.ctx.non_null_ptr_vars):
                    expr.ptr_non_null = True
                # Propagate readonly: accessing a non-value field through a
                # readonly reference yields a readonly result.
                if is_readonly_obj and not result.is_value_type():
                    result = ReadonlyType(unwrap_readonly(result))
                return result

            deref_target = self.get_deref_target_type(current_type)
            if deref_target is None:
                break
            current_type = deref_target
            deref_depth += 1

        if isinstance(actual_type, NamedType) and actual_type.is_record:
            raise self.ctx.error(f"Record '{actual_type.name}' has no field '{expr.field}'", expr)
        raise self.ctx.error(f"Cannot access field '{expr.field}' on type {obj_type}", expr)

    def _analyze_array_literal(
        self, expr: TpyArrayLiteral, expected_elem: TpyType | None = None
    ) -> TpyType:
        """Analyze an array literal [expr, expr, ...]

        In function-local contexts, returns a PendingListType that will be
        resolved to Array or list based on usage (mutation, parameter passing).
        In global/module context, returns ListType directly.

        Args:
            expected_elem: When provided (from a type annotation or return type hint),
                each element is checked against this type instead of against the first
                element. Enables mixed-type literals like [Int32(1), None] when the
                annotation is list[Int32 | None].
        """
        if not expr.elements:
            raise self.ctx.error("Empty array literal requires explicit type annotation", expr)

        # Analyze all elements first
        elem_types = [self.analyze_expr(e) for e in expr.elements]

        if expected_elem is not None:
            # Contextual mode: check each element against expected element type
            first_type = expected_elem
            for i, elem_type in enumerate(elem_types, 1):
                if elem_type == expected_elem:
                    continue
                try:
                    self.compat.check_type_compatible(
                        elem_type, expected_elem,
                        f"array literal element {i}",
                        expr.loc
                    )
                except SemanticError:
                    raise self.ctx.error(
                        f"List literal element {i} has type {elem_type}, "
                        f"incompatible with annotated element type {expected_elem}", expr
                    )
        else:
            # Inferred mode: check all elements against first element's type
            first_type = elem_types[0]
            # Keep IntLiteralType so array can coerce to either Int32 or BigInt based on context

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
                    raise self.ctx.error(
                        f"List literal has mixed types: element {i} is {elem_type}, "
                        f"but earlier elements are {first_type}. "
                        f"Use a type annotation like list[{first_type} | {elem_type}]", expr
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
            raise self.ctx.error(f"List repetition count must be an integer type, got {count_type}", expr)

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
                raise self.ctx.error(f"List repetition element {i} has type {elem_type}, expected {first_type}", expr)

        # Keep IntLiteralType so it can coerce to annotated type (list[Int32] or list[int])
        return ListType(first_type)

    def _analyze_subscript(self, expr: TpySubscript) -> TpyType:
        """Analyze subscript indexing: obj[index]"""
        obj_type = self.analyze_expr(expr.obj)
        index_type = self.analyze_expr(expr.index)

        if not isinstance(index_type, (Int32Type, BigIntType, IntLiteralType)):
            raise self.ctx.error(f"Subscript index must be an integer type, got {index_type}", expr)

        # Unwrap ReadonlyType, remember the flag
        is_readonly_obj = isinstance(obj_type, ReadonlyType)
        actual_type = unwrap_readonly(obj_type)

        # Optional[T] index access uses runtime null checks for unproven access.
        if isinstance(actual_type, OptionalType):
            if actual_type.inner.is_value_type():
                raise self.ctx.error(f"Cannot index type {obj_type}", expr)
            self.ctx.warning(OPTIONAL_NONE_ACCESS_WARNING, expr)
            expr.needs_optional_runtime_check = True
            actual_type = actual_type.inner

        # Use get_element_type() trait for containers and strings
        elem_type = actual_type.get_element_type()
        if elem_type is not None:
            if is_readonly_obj and not elem_type.is_value_type():
                elem_type = ReadonlyType(unwrap_readonly(elem_type))
            return elem_type

        # Protocol types - lookup __getitem__ return type
        if is_protocol_type(actual_type):
            ret = self.narrowing._get_protocol_getitem_type(actual_type)
            if ret is None:
                raise self.ctx.error(f"Protocol {actual_type.name} does not support indexing", expr)
            if is_readonly_obj and not ret.is_value_type():
                ret = ReadonlyType(unwrap_readonly(ret))
            return ret

        # User records with __getitem__ method
        if isinstance(actual_type, NamedType) and actual_type.is_user_record:
            ret = self.narrowing._get_record_getitem_type(actual_type)
            if ret is None:
                raise self.ctx.error(f"Cannot index type {actual_type}: no __getitem__ method", expr)
            if is_readonly_obj and not ret.is_value_type():
                ret = ReadonlyType(unwrap_readonly(ret))
            return ret

        raise self.ctx.error(f"Cannot index type {obj_type}", expr)
