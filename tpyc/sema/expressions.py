"""
TurboPython Expression Analysis

Core expression analysis including literals, names, operators, field access, and subscripts.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, FloatType, Float32Type, BoolType, StrType, CharType,
    NamedType, PtrType, OwnType, ListType, DictType, PendingListType, ListRepeatType, TupleType, SpanType,
    TypeParamRef, TypeParamKind, ListLiteralInfo, NoneType, OptionalType, UnionType,
    ReadonlyType, unwrap_readonly, EnumType, IntEnumType, is_any_str_type, PendingStrType,
    FixedIntType, StringType, StrViewType, make_union,
    ResolvedBinop, FunctionInfo, ParamInfo,
    INT32, FLOAT, STR, STRVIEW, CHAR, BOOL, BIGINT, NONE, is_protocol_type, container_to_str_template,
)
from ..parse import (
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    TpyFStringValue, TpyFString, FSTRING_CONV_REPR, FSTRING_CONV_STR,
    TpyBoolLiteral,
    TpyNoneLiteral, TpyName, TpyBinOp, TpyUnaryOp, TpyTypeParamConstruct,
    TpyCall, TpyMethodCall, TpyFieldAccess,
    TpyArrayLiteral, TpyTupleLiteral, TpyDictLiteral, TpyListRepeat, TpySlice, TpySubscript, TpyCoerce,
    TpyIfExpr,
)
from ..namespace import BindingKind
from ..coercions import CoercionContext
from .diagnostics import SemanticError, OPTIONAL_NONE_ACCESS_WARNING
from .narrowing import NarrowingTracker
from .numeric_lattice import widen_numeric_types

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
            self.narrowing.invalidate_field_facts_for_call(expr)
        elif isinstance(expr, TpyMethodCall):
            typ = self.methods.analyze_method_call(expr)
            self.narrowing.invalidate_field_facts_for_method_call(expr)
        elif isinstance(expr, TpyFieldAccess):
            typ = self._analyze_field_access(expr)
        elif isinstance(expr, TpyArrayLiteral):
            typ = self._analyze_array_literal(expr)
        elif isinstance(expr, TpyTupleLiteral):
            typ = self._analyze_tuple_literal(expr)
        elif isinstance(expr, TpyDictLiteral):
            typ = self._analyze_dict_literal(expr)
        elif isinstance(expr, TpyListRepeat):
            typ = self._analyze_list_repeat(expr)
        elif isinstance(expr, TpySubscript):
            typ = self._analyze_subscript(expr)
        elif isinstance(expr, TpyFString):
            typ = self._analyze_fstring(expr)
        elif isinstance(expr, TpyTypeParamConstruct):
            self.ctx.warning(
                "T() default-construction syntax is not supported in CPython; "
                "use make_default() from tpy instead", expr)
            typ = TypeParamRef(expr.param_name)
        elif isinstance(expr, TpyIfExpr):
            typ = self._analyze_if_expr(expr)
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

        # Ternary expression: propagate hint to both branches
        if isinstance(expr, TpyIfExpr):
            typ = self._analyze_if_expr(expr, type_hint=type_hint)
            self.ctx.set_expr_type(expr, typ)
            return typ

        # T() default-construction: resolves to whatever T maps to
        if isinstance(expr, TpyTypeParamConstruct):
            self.ctx.warning(
                "T() default-construction syntax is not supported in CPython; "
                "use make_default() from tpy instead", expr)
            self.ctx.set_expr_type(expr, type_hint)
            return type_hint

        # Tuple literal with TupleType hint: pass per-element hints
        if isinstance(expr, TpyTupleLiteral) and isinstance(type_hint, TupleType):
            if len(expr.elements) == len(type_hint.element_types):
                hints = list(type_hint.element_types)
                typ = self._analyze_tuple_literal(expr, element_hints=hints)
                self.ctx.set_expr_type(expr, typ)
                return typ

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

        # Non-empty dict literal with dict type hint
        if isinstance(expr, TpyDictLiteral) and expr.keys:
            inner_hint = unwrap_readonly(type_hint)
            if isinstance(inner_hint, OwnType):
                inner_hint = inner_hint.wrapped
            if isinstance(inner_hint, DictType):
                result = self._analyze_dict_literal(expr, inner_hint.key_type, inner_hint.value_type)
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
                if isinstance(other, PtrType):
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
            return isinstance(t, (Int32Type, BigIntType, IntLiteralType, FloatType, Float32Type))

        # Identity operators (is / is not) -- only valid with None or enums
        if expr.op in ("is", "is not"):
            # Unwrap ReadonlyType for nullable checks.
            left_check = unwrap_readonly(left_type)
            right_check = unwrap_readonly(right_type)
            # Enum identity: lower to ==/!=
            if isinstance(left_check, EnumType) and isinstance(right_check, EnumType):
                if left_check.name == right_check.name:
                    expr.op = "==" if expr.op == "is" else "!="
                    return BOOL
                raise self.ctx.error(
                    f"Cannot compare enum types '{left_check.name}' and '{right_check.name}'",
                    expr,
                )
            nullable_types = (OptionalType, PtrType)
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

        # Enum operators: base Enum supports == and != only;
        # IntEnum also supports ordering, comparison with integers, and arithmetic
        if isinstance(left_effective, EnumType) or isinstance(right_effective, EnumType):
            left_is_int_enum = isinstance(left_effective, IntEnumType)
            right_is_int_enum = isinstance(right_effective, IntEnumType)
            is_comparison = expr.op in ("==", "!=", "<", ">", "<=", ">=")

            if is_comparison:
                # IntEnum vs integer: coerce enum to underlying type
                if left_is_int_enum and isinstance(right_effective, (IntLiteralType, FixedIntType, BigIntType)):
                    expr.int_enum_coercion = left_effective
                    return BOOL
                if right_is_int_enum and isinstance(left_effective, (IntLiteralType, FixedIntType, BigIntType)):
                    expr.int_enum_coercion = right_effective
                    return BOOL
                if isinstance(left_effective, EnumType) and isinstance(right_effective, EnumType):
                    if left_effective.name != right_effective.name:
                        raise self.ctx.error(
                            f"Cannot compare enum types '{left_effective.name}' and '{right_effective.name}'",
                            expr,
                        )
                    # IntEnum supports ordering; base Enum does not
                    if expr.op in ("<", ">", "<=", ">=") and not left_is_int_enum:
                        raise self.ctx.error(
                            f"Ordering operators not supported for enum type '{left_effective.name}'",
                            expr,
                        )
                    # IntEnum ordering needs cast to underlying type
                    if left_is_int_enum and expr.op in ("<", ">", "<=", ">="):
                        expr.int_enum_coercion = left_effective
                    return BOOL
                # One side is enum, other is not (and not int for IntEnum)
                enum_name = left_effective.name if isinstance(left_effective, EnumType) else right_effective.name
                raise self.ctx.error(
                    f"Cannot compare '{enum_name}' with '{right_effective if isinstance(left_effective, EnumType) else left_effective}'",
                    expr,
                )

            # Non-comparison ops: IntEnum arithmetic is handled below;
            # base Enum in arithmetic is an error
            if not left_is_int_enum and not right_is_int_enum:
                enum_name = left_effective.name if isinstance(left_effective, EnumType) else right_effective.name
                raise self.ctx.error(
                    f"Operator '{expr.op}' not supported for enum type '{enum_name}'",
                    expr,
                )

        # Tuple comparison: == and != only, same length, element-wise compatible
        if isinstance(left_effective, TupleType) or isinstance(right_effective, TupleType):
            if expr.op in ("==", "!="):
                if not (isinstance(left_effective, TupleType) and isinstance(right_effective, TupleType)):
                    raise self.ctx.error(
                        f"Cannot compare {left_effective} with {right_effective}",
                        expr,
                    )
                if len(left_effective.element_types) != len(right_effective.element_types):
                    raise self.ctx.error(
                        f"Cannot compare tuples of different lengths: "
                        f"{left_effective} vs {right_effective}",
                        expr,
                    )
                for i, (lt, rt) in enumerate(zip(
                    left_effective.element_types, right_effective.element_types
                )):
                    if lt != rt:
                        try:
                            self.compat.check_type_compatible(lt, rt, "tuple comparison", source_expr=expr)
                        except SemanticError:
                            try:
                                self.compat.check_type_compatible(rt, lt, "tuple comparison", source_expr=expr)
                            except SemanticError:
                                raise self.ctx.error(
                                    f"Cannot compare tuple element {i}: "
                                    f"{lt} vs {rt}",
                                    expr,
                                )
                return BOOL
            raise self.ctx.error(
                f"Operator '{expr.op}' is not supported for tuple types",
                expr,
            )

        # Comparison operators return Bool
        if expr.op in ("==", "!=", "<", ">", "<=", ">="):
            return BOOL

        # Membership operators (in, not in) return Bool
        if expr.op in ("in", "not in"):
            # Dict membership checks key type
            if isinstance(right_type, DictType):
                self.compat.check_type_compatible(
                    left_type, right_type.key_type,
                    f"dict key (expected {right_type.key_type})",
                    loc=expr.loc,
                )
                return BOOL
            # Right side must be iterable (intrinsically or via NativeIterable protocol)
            from .list_literals import IterableHelper
            helper = IterableHelper(self.ctx)
            if helper.is_type_iterable(right_type):
                # For string containers, LHS must be str or Char
                if is_any_str_type(right_type):
                    if not (is_any_str_type(left_type) or isinstance(left_type, CharType)):
                        raise SemanticError(
                            f"Cannot check '{left_type}' membership in str (expected str or Char)",
                            expr.loc
                        )
                return BOOL
            raise self.ctx.error(f"Cannot use '{expr.op}' with non-iterable type {right_type}", expr)

        # Logical operators return Bool
        if expr.op in ("&&", "||"):
            return BOOL

        # IntEnum arithmetic: coerce to underlying type, delegate to standard binop
        if expr.op in ("+", "-", "*", "//", "%"):
            int_enum_side = None
            other_side = None
            if isinstance(left_effective, IntEnumType):
                int_enum_side = left_effective
                other_side = right_effective
            elif isinstance(right_effective, IntEnumType):
                int_enum_side = right_effective
                other_side = left_effective
            if int_enum_side is not None:
                if isinstance(other_side, IntEnumType):
                    if other_side.name != int_enum_side.name:
                        raise self.ctx.error(
                            f"Cannot mix arithmetic between '{int_enum_side.name}' "
                            f"and '{other_side.name}'",
                            expr,
                        )
                if isinstance(other_side, (IntEnumType, IntLiteralType, FixedIntType, BigIntType)):
                    # Coerce IntEnum operands to underlying type so standard
                    # FixedInt binop resolution (with checked arithmetic) handles it
                    expr.int_enum_coercion = int_enum_side
                    if isinstance(left_effective, IntEnumType):
                        left_effective = left_effective.underlying_type
                    if isinstance(right_effective, IntEnumType):
                        right_effective = right_effective.underlying_type
                    # Fall through to standard binop resolution below

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
            # List concat produces a list -- mark pending literals as mutated
            if isinstance(result.method.return_type, ListType):
                self._mark_list_concat_operands_mutated(expr, left_effective, right_effective)
            return result.method.return_type

        # Record types (user-defined or module) with dunder methods
        if isinstance(left_effective, NamedType) and left_effective.is_record:
            method_name = builtin_modules.BINOP_TO_METHOD.get(expr.op)
            if method_name:
                record = self.ctx.registry.get_record_for_type(left_effective)
                if record and (method := record.get_method(method_name)):
                    # Check parameter count and type
                    if len(method.params) == 1:
                        _, param_type = method.params[0]
                        # Substitute type params for generic types (e.g. list[T].__add__(list[T]))
                        type_subst = self.type_ops.build_type_substitution(left_effective)
                        if type_subst:
                            param_type = self.type_ops.substitute_types(param_type, type_subst)
                        if param_type == right_effective:
                            ret_type = method.return_type
                            if type_subst:
                                ret_type = self.type_ops.substitute_types(ret_type, type_subst)
                            # Build ResolvedBinop so codegen uses the method's
                            # cpp_template instead of raw C++ operator syntax.
                            cpp = method.cpp_template
                            if not cpp:
                                from .operators import DUNDER_CPP_TEMPLATES
                                cpp = DUNDER_CPP_TEMPLATES.get(method_name)
                            resolved_method = FunctionInfo(
                                name=method_name,
                                params=[ParamInfo(n, t) for n, t in method.params],
                                return_type=ret_type,
                                cpp_template=cpp,
                                is_method=True,
                            )
                            expr.resolved_binop = ResolvedBinop(
                                method=resolved_method,
                                left_wrapper="{expr}",
                                right_wrapper="{expr}",
                                receiver_type=left_effective,
                            )
                            return ret_type

        raise SemanticError(
            f"Invalid operand types for '{expr.op}': {left_type} and {right_type}",
            expr.loc,
        )

    def _mark_list_concat_operands_mutated(
        self, expr: TpyBinOp, left_type: TpyType, right_type: TpyType,
    ) -> None:
        """Mark PendingListType operands as mutated so they resolve to list, not Array."""
        for sub_expr, sub_type in ((expr.left, left_type), (expr.right, right_type)):
            if isinstance(sub_type, PendingListType):
                info = self.ctx.list_literals.get(sub_type.literal_id)
                if info:
                    info.is_mutated = True
            elif isinstance(sub_expr, TpyName):
                var_name = sub_expr.name
                if var_name in self.ctx.variable_to_literal:
                    lit_id = self.ctx.variable_to_literal[var_name]
                    info = self.ctx.list_literals.get(lit_id)
                    if info:
                        info.is_mutated = True

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
            if isinstance(effective_type, (BoolType, Int32Type, BigIntType, FloatType, Float32Type, IntLiteralType, OptionalType, EnumType)):
                return BOOL
            record = self.ctx.registry.get_record_for_type(effective_type)
            if record and (record.get_method_overloads("__bool__")
                           or record.get_method_overloads("__len__")):
                return BOOL
            raise self.ctx.error(f"Invalid operand type for 'not': {operand_type} (expected bool, numeric, or type with __bool__/__len__)", expr)

        # Float types support unary negation
        if isinstance(effective_type, (FloatType, Float32Type)):
            if expr.op == "-":
                if result := self.operators.resolve_unaryop(effective_type, expr.op):
                    expr.resolved_unaryop = result
                return effective_type

        # IntEnum: unary negation returns the underlying integer type
        if isinstance(effective_type, IntEnumType) and expr.op == "-":
            return effective_type.underlying_type

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
        if isinstance(typ, NamedType) and typ.is_record:
            record = self.ctx.registry.get_record_for_type(typ)
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

                # Enum type-level member access: Color.Red -> EnumType
                if binding and binding.kind == BindingKind.ENUM:
                    enum_type = binding.enum_type
                    if expr.field in enum_type.members:
                        return enum_type
                    raise self.ctx.error(
                        f"Enum '{enum_type.name}' has no member '{expr.field}'", expr)

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

        # Enum instance property access: c.name -> str, c.value -> underlying type
        if isinstance(actual_type, EnumType):
            if expr.field == "name":
                return STR
            elif expr.field == "value":
                return actual_type.underlying_type
            raise self.ctx.error(
                f"Enum value of type '{actual_type.name}' has no attribute '{expr.field}'. "
                f"Use '{actual_type.name}.{expr.field}' to access enum members", expr)

        # Deref chain loop -- resolves through Ptr, ReadOnlyPtr, and any Deref[T] type
        current_type = actual_type
        deref_depth = 0
        while deref_depth <= 8:
            result = self._try_find_field(current_type, expr)
            if result is not None:
                expr.deref_depth = deref_depth
                if (deref_depth > 0
                        and isinstance(actual_type, PtrType)
                        and isinstance(expr.obj, TpyName)
                        and expr.obj.name in self.ctx.non_null_ptr_vars):
                    expr.ptr_non_null = True
                # Propagate readonly: accessing a non-value field through a
                # readonly reference yields a readonly result.
                # Ptr[T] fields become ReadOnlyPtr[T], Span[T] -> ReadOnlySpan[T].
                if is_readonly_obj:
                    if isinstance(result, PtrType) and not result.is_readonly:
                        result = result.as_const()
                    elif isinstance(result, SpanType) and not result.is_readonly:
                        result = result.as_const()
                    elif not result.is_value_type():
                        result = ReadonlyType(unwrap_readonly(result))
                # Apply field path narrowing (e.g. after `if obj.field is not None:`)
                if isinstance(expr.obj, TpyName):
                    field_key = f"{expr.obj.name}.{expr.field}"
                    narrowed = self.ctx.narrowed_types.get(field_key)
                    if narrowed is not None:
                        result = narrowed
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

    _HASHABLE = NamedType("Hashable", is_protocol=True)

    def _validate_dict_key_type(self, key_type: TpyType, expr: TpyExpr) -> None:
        """Validate that a type can be used as a dict key."""
        if isinstance(key_type, IntLiteralType):
            return
        if isinstance(key_type, (EnumType, IntEnumType)):
            return
        if isinstance(key_type, PendingStrType):
            return
        # User records: allow frozen dataclasses (have synthesized __hash__ + __eq__)
        if isinstance(key_type, NamedType) and key_type.is_user_record:
            info = self.ctx.registry.get_record(key_type.name)
            if not (info is not None and info.is_frozen
                    and "__hash__" in info.methods and "__eq__" in info.methods):
                raise self.ctx.error(
                    f"Type '{key_type}' cannot be used as a dict key "
                    f"(requires @dataclass(frozen=True) for __hash__ support)", expr,
                )
            return
        if self.protocols.type_conforms_to_protocol(key_type, self._HASHABLE):
            return
        raise self.ctx.error(
            f"Type '{key_type}' cannot be used as a dict key (not hashable)", expr,
        )

    # -- Ternary expression analysis ------------------------------------------

    def _analyze_if_expr(
        self, expr: TpyIfExpr, type_hint: TpyType | None = None,
    ) -> TpyType:
        """Analyze a ternary conditional: then_expr if condition else else_expr."""
        self.analyze_expr(expr.condition)
        self.narrowing.warn_truthy_value_optionals(expr.condition)

        then_facts, else_facts = self.narrowing.condition_type_facts(
            expr.condition)

        # Save narrowed_types (ternary doesn't create vars, so we only
        # need to save/restore narrowing, not the full InitTracker state).
        saved_narrowed = dict(self.ctx.narrowed_types)

        self.ctx.narrowed_types.update(then_facts)
        if type_hint is not None:
            then_type = self.analyze_expr_with_hint(expr.then_expr, type_hint)
        else:
            then_type = self.analyze_expr(expr.then_expr)

        self.ctx.narrowed_types = dict(saved_narrowed)
        self.ctx.narrowed_types.update(else_facts)
        if type_hint is not None:
            else_type = self.analyze_expr_with_hint(expr.else_expr, type_hint)
        else:
            else_type = self.analyze_expr(expr.else_expr)

        self.ctx.narrowed_types = saved_narrowed

        common = self._ternary_common_type(expr, then_type, else_type,
                                           widen_numeric_types)

        # Coerce branches to the common type so C++ ternary has
        # matching branch types (e.g. None -> std::optional<T>).
        if then_type != common:
            expr.then_expr = self.compat.coerce_expr(
                expr.then_expr, then_type, common,
                "ternary branch", coercion_ctx=CoercionContext.INIT)
        if else_type != common:
            expr.else_expr = self.compat.coerce_expr(
                expr.else_expr, else_type, common,
                "ternary branch", coercion_ctx=CoercionContext.INIT)

        return common

    def _ternary_common_type(
        self, expr: TpyIfExpr,
        then_type: TpyType, else_type: TpyType,
        widen_numeric_types: object,
    ) -> TpyType:
        """Compute the common result type of a ternary expression's branches."""
        if then_type == else_type:
            return then_type

        t, e = then_type, else_type

        # IntLiteral resolution
        if isinstance(t, IntLiteralType) and isinstance(e, IntLiteralType):
            return self.ctx.default_int_for_literal(t, expr.then_expr)
        if isinstance(t, IntLiteralType):
            if isinstance(e, (FixedIntType, BigIntType, FloatType, Float32Type)):
                return e
            t = self.ctx.default_int_for_literal(t, expr.then_expr)
        if isinstance(e, IntLiteralType):
            if isinstance(t, (FixedIntType, BigIntType, FloatType, Float32Type)):
                return t
            e = self.ctx.default_int_for_literal(e, expr.else_expr)

        if t == e:
            return t

        # Numeric widening (Int32 + Int64 -> Int64, etc.)
        widened = widen_numeric_types(t, e)
        if widened is not None:
            return widened

        # T + None / None + T -> Optional[T]
        if isinstance(e, NoneType):
            return make_union(t, NoneType())
        if isinstance(t, NoneType):
            return make_union(e, NoneType())

        raise self.ctx.error(
            f"Incompatible types in ternary expression: "
            f"'{t}' and '{e}'",
            expr,
        )

    def _analyze_dict_literal(
        self, expr: TpyDictLiteral,
        expected_key: TpyType | None = None,
        expected_value: TpyType | None = None,
    ) -> TpyType:
        """Analyze a dict literal {key: value, ...}"""
        if not expr.keys:
            # Empty dict needs type annotation (handled at assignment site)
            raise self.ctx.error(
                "Empty dict literal requires type annotation "
                "(e.g. d: dict[str, int] = {})", expr,
            )

        if expected_key:
            key_types = [self.analyze_expr_with_hint(k, expected_key) for k in expr.keys]
        else:
            key_types = [self.analyze_expr(k) for k in expr.keys]
        if expected_value:
            value_types = [self.analyze_expr_with_hint(v, expected_value) for v in expr.values]
        else:
            value_types = [self.analyze_expr(v) for v in expr.values]

        # Unify key types
        key_type = key_types[0]
        for i, kt in enumerate(key_types[1:], 2):
            if isinstance(key_type, IntLiteralType) and isinstance(kt, IntLiteralType):
                continue
            if isinstance(kt, IntLiteralType) and isinstance(key_type, (Int32Type, BigIntType)):
                continue
            if isinstance(key_type, IntLiteralType) and isinstance(kt, (Int32Type, BigIntType)):
                key_type = kt
                continue
            if kt != key_type:
                raise self.ctx.error(
                    f"Dict has mixed key types: key {i} is {kt}, "
                    f"but earlier keys are {key_type}", expr,
                )

        # Unify value types
        value_type = value_types[0]
        for i, vt in enumerate(value_types[1:], 2):
            if isinstance(value_type, IntLiteralType) and isinstance(vt, IntLiteralType):
                continue
            if isinstance(vt, IntLiteralType) and isinstance(value_type, (Int32Type, BigIntType)):
                continue
            if isinstance(value_type, IntLiteralType) and isinstance(vt, (Int32Type, BigIntType)):
                value_type = vt
                continue
            if vt != value_type:
                raise self.ctx.error(
                    f"Dict has mixed value types: value {i} is {vt}, "
                    f"but earlier values are {value_type}", expr,
                )

        # Use annotation types when literal elements are IntLiteralType
        if isinstance(key_type, IntLiteralType):
            key_type = expected_key if expected_key else self.ctx.default_int_for_literal(key_type)
        if isinstance(value_type, IntLiteralType):
            value_type = expected_value if expected_value else self.ctx.default_int_for_literal(value_type)

        self._validate_dict_key_type(key_type, expr)
        return DictType(key_type, value_type)

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

        # Global context -> ListType (no deferred resolution)
        if self.ctx.current_function is None:
            return ListType(first_type)

        # Function-local context -> PendingListType for deferred resolution
        # Compute size if count is compile-time constant
        if isinstance(expr.count, TpyIntLiteral):
            size = len(expr.elements) * expr.count.value
        else:
            size = -1  # Variable count -- cannot resolve to Array

        literal_id = self.ctx.literal_counter
        self.ctx.literal_counter += 1

        info = ListLiteralInfo(
            literal_id=literal_id,
            expr=expr,
            element_type=first_type,
            size=size,
            is_global=self.ctx.is_top_level,
        )
        self.ctx.list_literals[literal_id] = info
        self.ctx.pending_resolutions.append(literal_id)

        return PendingListType(first_type, size, literal_id)

    def _analyze_tuple_literal(
        self, expr: TpyTupleLiteral, element_hints: list[TpyType | None] | None = None
    ) -> TupleType:
        """Analyze a tuple literal (expr, expr, ...)."""
        elem_types = []
        for i, elem in enumerate(expr.elements):
            hint = element_hints[i] if element_hints and i < len(element_hints) else None
            if hint is not None:
                analyzed = self.analyze_expr_with_hint(elem, hint)
                # Preserve Own[] from hint when the analyzed type matches
                if isinstance(hint, OwnType) and not isinstance(analyzed, OwnType):
                    analyzed = OwnType(analyzed)
                elem_types.append(analyzed)
            else:
                elem_types.append(self.analyze_expr(elem))
        return TupleType(tuple(elem_types))

    def _analyze_tuple_subscript(self, expr: TpySubscript, tuple_type: TupleType) -> TpyType:
        """Analyze tuple subscript: t[0], t[-1] with compile-time constant index."""
        index = expr.index
        n = len(tuple_type.element_types)
        # Register index type for codegen
        self.analyze_expr(index)
        # Extract compile-time index
        if isinstance(index, TpyIntLiteral):
            idx = index.value
        elif (isinstance(index, TpyUnaryOp) and index.op == "-"
              and isinstance(index.operand, TpyIntLiteral)):
            idx = -index.operand.value
        else:
            raise self.ctx.error(
                "Tuple index must be a compile-time integer literal", expr
            )
        # Resolve negative index
        original_idx = idx
        if idx < 0:
            idx += n
        # Range check
        if idx < 0 or idx >= n:
            raise self.ctx.error(
                f"Tuple index {original_idx} out of range for "
                f"tuple[{', '.join(str(t) for t in tuple_type.element_types)}] "
                f"(length {n})",
                expr,
            )
        return tuple_type.element_types[idx]

    def _analyze_subscript(self, expr: TpySubscript) -> TpyType:
        """Analyze subscript indexing: obj[index] or slicing: obj[start:stop]"""
        # Enum name lookup: Color["Red"] -> Color (panics on invalid)
        if isinstance(expr.obj, TpyName) and self.ctx.current_ns:
            binding = self.ctx.current_ns.lookup(expr.obj.name)
            if binding and binding.kind == BindingKind.ENUM:
                index_type = self.analyze_expr(expr.index)
                if not is_any_str_type(index_type):
                    raise self.ctx.error(
                        f"Enum subscript index must be a string, got '{index_type}'",
                        expr,
                    )
                expr.enum_from_name = binding.enum_type
                return binding.enum_type

        obj_type = self.analyze_expr(expr.obj)

        # Tuple indexing: t[0], t[-1] -- compile-time constant index only
        actual_for_tuple = unwrap_readonly(obj_type)
        if isinstance(actual_for_tuple, TupleType):
            return self._analyze_tuple_subscript(expr, actual_for_tuple)

        # Slice: obj[start:stop]
        if isinstance(expr.index, TpySlice):
            return self._analyze_slice(expr, obj_type)

        index_type = self.analyze_expr(expr.index)

        # Dict subscript: d[key] -> V (key can be non-integer)
        actual_obj = unwrap_readonly(obj_type)
        if isinstance(actual_obj, DictType):
            self.compat.check_type_compatible(
                index_type, actual_obj.key_type,
                f"dict key (expected {actual_obj.key_type})",
                loc=expr.loc,
            )
            return actual_obj.value_type

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
            # Subscript on a repeat-sourced pending list needs indexing support
            # (repeat_range doesn't have operator[], but Array does).
            if isinstance(actual_type, PendingListType):
                info = self.ctx.list_literals.get(actual_type.literal_id)
                if info and isinstance(info.expr, TpyListRepeat):
                    info.needs_indexing = True
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

        # Records with __getitem__ method
        if isinstance(actual_type, NamedType) and actual_type.is_record:
            ret = self.narrowing._get_record_getitem_type(actual_type)
            if ret is None:
                raise self.ctx.error(f"Cannot index type {actual_type}: no __getitem__ method", expr)
            if is_readonly_obj and not ret.is_value_type():
                ret = ReadonlyType(unwrap_readonly(ret))
            return ret

        raise self.ctx.error(f"Cannot index type {obj_type}", expr)

    _SLICEABLE_STR_TYPES = (StrType, StringType, StrViewType, PendingStrType)

    def _analyze_slice(self, expr: TpySubscript, obj_type: TpyType) -> TpyType:
        """Analyze slice expression: obj[start:stop]"""
        sl = expr.index
        assert isinstance(sl, TpySlice)
        for bound, label in ((sl.lower, "start"), (sl.upper, "stop")):
            if bound is not None:
                bound_type = self.analyze_expr(bound)
                if not isinstance(bound_type, (Int32Type, BigIntType, IntLiteralType)):
                    raise self.ctx.error(
                        f"Slice {label} must be an integer type, got {bound_type}", bound
                    )
        actual_type = unwrap_readonly(obj_type)
        # TODO: Optional[str] after narrowing passes this check but codegen
        # doesn't emit .value() -- same pre-existing issue as single-index subscript.
        if not isinstance(actual_type, self._SLICEABLE_STR_TYPES):
            raise self.ctx.error(f"Slicing is not yet supported for {obj_type}", expr)
        return STRVIEW

    _FORMATTABLE_TYPES = (
        FixedIntType, BigIntType, IntLiteralType, FloatType, Float32Type, BoolType,
        StrType, StringType, StrViewType, PendingStrType, CharType, EnumType,
    )

    _STRINGABLE = NamedType("Stringable", is_protocol=True)
    _REPRESENTABLE = NamedType("Representable", is_protocol=True)

    def _analyze_fstring(self, expr: TpyFString) -> TpyType:
        """Analyze f-string parts and return STR (owned string)."""
        for part in expr.parts:
            if isinstance(part, TpyFStringValue):
                part_type = self.analyze_expr(part.expr)
                resolved = unwrap_readonly(part_type)
                conv = part.conversion

                if container_to_str_template(resolved) is not None:
                    pass  # containers have runtime to_str
                elif conv == FSTRING_CONV_REPR:
                    if not self.protocols.type_conforms_to_protocol(resolved, self._REPRESENTABLE):
                        raise self.ctx.error(
                            f"Type {part_type} cannot use !r conversion (no __repr__ method)",
                            part.expr,
                        )
                elif conv == FSTRING_CONV_STR:
                    if not isinstance(resolved, self._FORMATTABLE_TYPES):
                        if not self.protocols.type_conforms_to_protocol(resolved, self._STRINGABLE):
                            if not self.protocols.type_conforms_to_protocol(resolved, self._REPRESENTABLE):
                                raise self.ctx.error(
                                    f"Type {part_type} cannot use !s conversion"
                                    " (no __str__ or __repr__ method)",
                                    part.expr,
                                )
                elif not isinstance(resolved, self._FORMATTABLE_TYPES):
                    if not self.protocols.type_conforms_to_protocol(resolved, self._STRINGABLE):
                        if not self.protocols.type_conforms_to_protocol(resolved, self._REPRESENTABLE):
                            raise self.ctx.error(
                                f"Type {part_type} cannot be used in f-string"
                                " (no __str__ or __repr__ method)",
                                part.expr,
                            )
                if part.format_spec is not None and isinstance(resolved, (BigIntType, IntLiteralType)):
                    raise self.ctx.error(
                        "Format specs on int are not yet supported (use a fixed-width type like Int32)",
                        part.expr,
                    )
        return STR
