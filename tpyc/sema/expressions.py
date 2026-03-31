"""
TurboPython Expression Analysis

Core expression analysis including literals, names, operators, field access, and subscripts.
"""

from __future__ import annotations
from contextlib import ExitStack
from collections.abc import Callable as CallableFn
from typing import Literal, TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, FloatType, Float32Type, FloatLiteralType, BoolType, StrType, CharType,
    NamedType, PtrType, OwnType, ListType, DictType, SetType, ArrayType, PendingListType, ListRepeatType, GenExprType, TupleType, SpanType,
    TypeParamRef, TypeParamKind, ListLiteralInfo, NoneType, OptionalType, UnionType, VoidType,
    ReadonlyType, unwrap_readonly, EnumType, IntEnumType, is_any_str_type, PendingStrType, PendingViewType,
    is_any_bytes_type, BytesType, ByteArrayType, BytesViewType, PendingBytesType,
    FixedIntType, StringType, StrViewType, make_union,
    ResolvedBinop, FunctionInfo, ParamInfo, UnknownElementType, UNKNOWN_ELEMENT,
    PendingDictType, PendingSetType, DictLiteralInfo,
    resolve_int_literals, FnType, CallableType,
    INT32, FLOAT, STR, STRVIEW, CHAR, BOOL, BIGINT, NONE, SLICE, BYTES, BYTESVIEW, UINT8,
    is_protocol_type, container_to_str_template, contains_type_param,
    PendingGenericInstanceType, SliceType, unwrap_ref_type,
)
from ..parse import (
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBytesLiteral,
    TpyFStringValue, TpyFString, FSTRING_CONV_REPR, FSTRING_CONV_STR,
    TpyBoolLiteral,
    TpyNoneLiteral, TpyName, TpyBinOp, TpyChainedCompare, TpyUnaryOp, TpyTypeParamConstruct,
    TpyCall, TpyMethodCall, TpyFieldAccess, TpyFunction,
    TpyArrayLiteral, TpyTupleLiteral, TpyDictLiteral, TpySetLiteral, TpyListRepeat,
    TpyListComprehension, TpyDictComprehension, TpySetComprehension, TpyGeneratorExpression, TpyComprehensionGenerator,
    TpySlice, TpySubscript, TpyCoerce,
    TpyIfExpr, TpyNamedExpr,
    TpyLambda,
    TpyStmt, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyForEach, TpyWith,
    TpyNestedDef,
    collect_name_refs,
)
from ..namespace import BindingKind
from ..coercions import CoercionContext
from ..prescan import _expr_to_narrowing_key
from .diagnostics import SemanticError, OPTIONAL_NONE_ACCESS_WARNING
from .narrowing import NarrowingTracker
from .numeric_lattice import widen_numeric_types
from .list_literals import IterableHelper
from .local_deduction import collect_pending_source_types

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .operators import OperatorResolver
    from .protocols import ProtocolChecker
    from .compatibility import TypeCompatibility
    from .calls import CallAnalyzer
    from .methods import MethodAnalyzer
    from .scope_tracker import ScopeTracker

from tpyc import modules as builtin_modules


def _walk_body_stmts(
    stmts: list[TpyStmt],
    on_expr: CallableFn[[TpyExpr], None],
    on_stmt: CallableFn[[TpyStmt], None],
) -> None:
    """Walk statements calling on_expr/on_stmt. Does NOT recurse into TpyNestedDef."""
    for stmt in stmts:
        on_stmt(stmt)
        if isinstance(stmt, TpyNestedDef):
            continue  # separate scope
        for expr in stmt.exprs():
            on_expr(expr)
        for body in stmt.sub_bodies():
            _walk_body_stmts(body, on_expr, on_stmt)


def _collect_body_name_refs(stmts: list[TpyStmt]) -> set[str]:
    """Collect all name references from a list of statements.

    Walks all expressions in statements to find free variable references.
    Does NOT recurse into nested function definitions (separate scope).
    """
    names: set[str] = set()

    def on_expr(expr: TpyExpr) -> None:
        names.update(collect_name_refs(expr))

    _walk_body_stmts(stmts, on_expr, lambda s: None)
    return names


def _collect_body_local_defs(stmts: list[TpyStmt]) -> set[str]:
    """Collect names defined locally in a statement body (not from enclosing scope)."""
    defs: set[str] = set()

    def on_stmt(stmt: TpyStmt) -> None:
        if isinstance(stmt, TpyVarDecl):
            defs.add(stmt.name)
        elif isinstance(stmt, TpyAssign):
            if isinstance(stmt.target, TpyName):
                defs.add(stmt.target.name)
        elif isinstance(stmt, TpyTupleUnpack):
            for name in stmt.targets:
                if name is not None:
                    defs.add(name)
        elif isinstance(stmt, TpyForEach):
            defs.add(stmt.var)
        elif isinstance(stmt, TpyWith):
            for item in stmt.items:
                if item.target is not None:
                    defs.add(item.target)
        elif isinstance(stmt, TpyNestedDef):
            defs.add(stmt.func.name)

    _walk_body_stmts(stmts, lambda e: None, on_stmt)
    return defs


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
        self.scopes: ScopeTracker | None = None

    def _resolve_literal_type(self, t: TpyType) -> TpyType:
        """Replace IntLiteralType/FloatLiteralType with concrete types for display."""
        if isinstance(t, IntLiteralType):
            return self.ctx.default_int_type
        if isinstance(t, FloatLiteralType):
            return FLOAT
        if isinstance(t, TupleType):
            resolved = tuple(self._resolve_literal_type(e) for e in t.element_types)
            if resolved != t.element_types:
                return TupleType(resolved)
        if isinstance(t, PendingListType):
            resolved_elem = self._resolve_literal_type(t.element_type)
            if resolved_elem is not t.element_type:
                return PendingListType(resolved_elem, t.size, t.literal_id)
        return t

    def _user_type_name(self, t: TpyType) -> str:
        """User-facing type name for error messages (resolves literal types)."""
        return str(self._resolve_literal_type(t))

    def set_cross_deps(self, calls: CallAnalyzer, methods: MethodAnalyzer,
                       scopes: ScopeTracker | None = None) -> None:
        """Wire circular dependencies (must be called before analyze_expr)."""
        self.calls = calls
        self.methods = methods
        if scopes is not None:
            self.scopes = scopes

    @staticmethod
    def _strip_ref_from_expr_type(typ: TpyType) -> TpyType:
        """Strip Ref from inside protocol type args (Iterator, Iterable, etc.).

        When a function returns Iterator[Ref[Point]], the Ref is about the
        iterator's internal storage (val_or_ref). Downstream consumers
        (enumerate, zip, etc.) should see the logical element type (Point),
        not the storage wrapper. Without this, enumerate(map(identity, pts))
        would get T=Ref[Point] -> val_or_ref<Point> in its template, which
        doesn't compose with the C++ enumerate implementation.
        """
        if (isinstance(typ, NamedType) and typ.is_protocol and typ.type_args):
            stripped = tuple(
                unwrap_ref_type(a) if isinstance(a, TpyType) else a
                for a in typ.type_args
            )
            if stripped != typ.type_args:
                return NamedType(typ.name, stripped, typ.is_protocol,
                                 typ._module_qname, typ.is_dynamic_protocol)
        return typ

    def analyze_expr(self, expr: TpyExpr) -> TpyType:
        """Analyze an expression and return its type."""
        if isinstance(expr, TpyIntLiteral):
            typ = IntLiteralType(expr.value)
        elif isinstance(expr, TpyFloatLiteral):
            typ = FloatLiteralType(expr.value)
        elif isinstance(expr, TpyStrLiteral):
            # String literals are always str type (including single-char)
            # Char type is only used when explicitly annotated or from string indexing
            typ = STR
        elif isinstance(expr, TpyBytesLiteral):
            typ = BYTES
        elif isinstance(expr, TpyBoolLiteral):
            typ = BOOL
        elif isinstance(expr, TpyNoneLiteral):
            typ = NONE
        elif isinstance(expr, TpyName):
            typ = self._analyze_name(expr)
        elif isinstance(expr, TpyBinOp):
            typ = self._analyze_binop(expr)
        elif isinstance(expr, TpyChainedCompare):
            typ = self._analyze_chained_compare(expr)
        elif isinstance(expr, TpyUnaryOp):
            typ = self._analyze_unaryop(expr)
        elif isinstance(expr, TpyCall):
            typ = self._strip_ref_from_expr_type(unwrap_ref_type(self.calls.analyze_call(expr)))
            self.narrowing.invalidate_field_facts_for_call(expr)
        elif isinstance(expr, TpyMethodCall):
            typ = self._strip_ref_from_expr_type(unwrap_ref_type(self.methods.analyze_method_call(expr)))
            self.narrowing.invalidate_field_facts_for_method_call(expr)
        elif isinstance(expr, TpyFieldAccess):
            typ = self._analyze_field_access(expr)
        elif isinstance(expr, TpyArrayLiteral):
            typ = self._analyze_array_literal(expr)
        elif isinstance(expr, TpyTupleLiteral):
            typ = self._analyze_tuple_literal(expr)
        elif isinstance(expr, TpyDictLiteral):
            typ = self._analyze_dict_literal(expr)
        elif isinstance(expr, TpySetLiteral):
            typ = self._analyze_set_literal(expr)
        elif isinstance(expr, TpyListRepeat):
            typ = self._analyze_list_repeat(expr)
        elif isinstance(expr, TpyListComprehension):
            typ = self._analyze_list_comprehension(expr)
        elif isinstance(expr, TpyDictComprehension):
            typ = self._analyze_dict_comprehension(expr)
        elif isinstance(expr, TpySetComprehension):
            typ = self._analyze_set_comprehension(expr)
        elif isinstance(expr, TpyGeneratorExpression):
            typ = self._analyze_generator_expression(expr)
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
        elif isinstance(expr, TpyNamedExpr):
            typ = self._analyze_named_expr(expr)
        elif isinstance(expr, TpyLambda):
            typ = self._analyze_lambda(expr)
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

        type_hint = unwrap_ref_type(type_hint)

        # Lambda with Fn/Callable type hint: infer param types from the hint
        if isinstance(expr, TpyLambda) and isinstance(type_hint, (FnType, CallableType)):
            typ = self._analyze_lambda_with_fn_hint(expr, type_hint)
            self.ctx.set_expr_type(expr, typ)
            return typ

        # Named function reference with Fn/Callable hint: resolve as function value.
        # Unwrap OwnType so that e.g. list.append(Own[Callable[...]]) works.
        fn_hint = type_hint.wrapped if isinstance(type_hint, OwnType) else type_hint
        if isinstance(expr, TpyName) and isinstance(fn_hint, (FnType, CallableType)):
            result = self._try_resolve_function_ref(expr, fn_hint)
            if result is not None:
                self.ctx.set_expr_type(expr, result)
                return result

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
        _generic_lookup = (builtin_modules.lookup_generic_type(expr.func_name)
                           if isinstance(expr, TpyCall) and isinstance(expr.func, TpyName) else None)
        is_generic_constructor = (isinstance(expr, TpyCall) and
                                  not expr.args and
                                  expr.call_type is None and
                                  _generic_lookup is not None and
                                  bool(_generic_lookup.type_def and _generic_lookup.type_def.type_params))

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
                lookup = builtin_modules.lookup_generic_type(expr.func_name)  # type: ignore
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

        # List comprehension with list type hint: propagate element type
        if isinstance(expr, TpyListComprehension):
            inner_hint = unwrap_readonly(type_hint)
            if isinstance(inner_hint, OwnType):
                inner_hint = inner_hint.wrapped
            if isinstance(inner_hint, ListType):
                typ = self._analyze_list_comprehension(expr, expected_elem=inner_hint.element_type)
                if isinstance(typ, PendingListType):
                    info = self.ctx.list_literals.get(typ.literal_id)
                    if info:
                        info.has_explicit_annotation = True
                        info.explicit_type = inner_hint
                self.ctx.set_expr_type(expr, typ)
                return typ
            if isinstance(inner_hint, ArrayType):
                typ = self._analyze_list_comprehension(expr, expected_elem=inner_hint.element_type)
                if isinstance(typ, PendingListType):
                    info = self.ctx.list_literals.get(typ.literal_id)
                    if info:
                        info.has_explicit_annotation = True
                        info.explicit_type = inner_hint
                self.ctx.set_expr_type(expr, typ)
                return typ

        # Dict comprehension with dict type hint: propagate key/value types
        if isinstance(expr, TpyDictComprehension):
            inner_hint = unwrap_readonly(type_hint)
            if isinstance(inner_hint, OwnType):
                inner_hint = inner_hint.wrapped
            if isinstance(inner_hint, DictType):
                typ = self._analyze_dict_comprehension(
                    expr, expected_key=inner_hint.key_type,
                    expected_value=inner_hint.value_type)
                self.ctx.set_expr_type(expr, typ)
                return typ

        # Non-empty dict literal with dict type hint
        if isinstance(expr, TpyDictLiteral) and expr.keys:
            inner_hint = unwrap_readonly(type_hint)
            if isinstance(inner_hint, OwnType):
                inner_hint = inner_hint.wrapped
            if isinstance(inner_hint, DictType):
                result = self._analyze_dict_literal(expr, inner_hint.key_type, inner_hint.value_type)
                self.ctx.set_expr_type(expr, result)
                return result

        # Set comprehension with set type hint: propagate element type
        if isinstance(expr, TpySetComprehension):
            inner_hint = unwrap_readonly(type_hint)
            if isinstance(inner_hint, OwnType):
                inner_hint = inner_hint.wrapped
            if isinstance(inner_hint, SetType):
                typ = self._analyze_set_comprehension(
                    expr, expected_elem=inner_hint.element_type)
                self.ctx.set_expr_type(expr, typ)
                return typ

        # Non-empty set literal with set type hint
        if isinstance(expr, TpySetLiteral) and expr.elements:
            inner_hint = unwrap_readonly(type_hint)
            if isinstance(inner_hint, OwnType):
                inner_hint = inner_hint.wrapped
            if isinstance(inner_hint, SetType):
                result = self._analyze_set_literal(expr, inner_hint.element_type)
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
        # Use-after-consume: variable was consumed by a consuming method call
        if expr.name in self.ctx.consumed_vars:
            raise self.ctx.error(
                f"Cannot use '{expr.name}' after it was consumed by a consuming method call",
                expr,
            )

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
                    result = self.narrowing.narrow_name_type(expr.name, binding.type)
                    # Strip Own[T] -- see comment below at scope fallback path.
                    return result.wrapped if isinstance(result, OwnType) else result
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
            # Lazy promotion of loop-scoped variables referenced after the loop
            if self._promote_pending_loop_var(expr.name):
                typ = self.ctx.current_scope.lookup(expr.name)
            else:
                raise self.ctx.error(f"Undefined variable: '{expr.name}'", expr)
        self._check_definitely_assigned(expr)
        result = self.narrowing.narrow_name_type(expr.name, typ)
        # Strip Own[T] for expression type -- Own indicates the variable owns
        # its storage (for movability), but the expression type is T (the
        # variable is an lvalue when used in expressions).
        if isinstance(result, OwnType):
            result = result.wrapped
        return result

    def _check_definitely_assigned(self, expr: TpyName) -> None:
        """Check that a local variable is definitely assigned before use."""
        if (not self.ctx.init_terminated
                and expr.name in self.ctx.var_scope_depth
                and self.ctx.var_scope_depth[expr.name] >= 1
                and expr.name not in self.ctx.definitely_assigned):
            raise self.ctx.error(
                f"variable '{expr.name}' may not be assigned at this point", expr)

    def _promote_pending_loop_var(self, name: str) -> bool:
        """Promote a pending for-loop-scoped variable if present.

        Returns True if the variable was promoted (added to scope and
        definitely_assigned, registered for codegen pre-declaration).
        """
        pending = self.ctx.pending_loop_vars.pop(name, None)
        if pending is None:
            return False
        var_type, loop_stmt, orig_stmt = pending
        self.ctx.current_scope.define(name, var_type)
        self.ctx.definitely_assigned.add(name)
        # Register for codegen pre-declaration
        decls = self.ctx.if_branch_decls.setdefault(id(loop_stmt), {})
        decls[name] = var_type
        # Mark the original for-loop's var for hoisted codegen (hidden counter)
        from ..parse import TpyForEach
        if isinstance(orig_stmt, TpyForEach) and name == orig_stmt.var:
            orig_stmt.hoist_loop_var = True
        return True

    def _normalize_pending_container(self, t: TpyType) -> TpyType:
        """Normalize a pending container type to a concrete type with resolved IntLiteralType elements.

        Used in or/and/ternary type comparison: two PendingListType literals with the same
        element type but different IDs (or different IntLiteralType values) are compatible.
        """
        if isinstance(t, PendingListType):
            return resolve_int_literals(ListType(t.element_type), self.ctx.default_int_for_literal)
        if isinstance(t, PendingDictType):
            k = self.ctx.default_int_for_literal(t.key_type) if isinstance(t.key_type, IntLiteralType) else t.key_type
            k = FLOAT if isinstance(k, FloatLiteralType) else k
            v = self.ctx.default_int_for_literal(t.value_type) if isinstance(t.value_type, IntLiteralType) else t.value_type
            v = FLOAT if isinstance(v, FloatLiteralType) else v
            return DictType(k, v)
        if isinstance(t, PendingSetType):
            elem = self.ctx.default_int_for_literal(t.element_type) if isinstance(t.element_type, IntLiteralType) else t.element_type
            elem = FLOAT if isinstance(elem, FloatLiteralType) else elem
            return SetType(elem)
        return t

    def _logical_op_result_type(self, left: TpyType, right: TpyType) -> TpyType:
        """Determine result type for and/or operators.

        Python semantics: `x and y` returns an operand, not bool.
        Same non-bool type -> return that type (enables value-context usage).
        Different types or bool operands -> return bool.
        """
        # Bool operands: C++ &&/|| already correct
        if isinstance(left, BoolType) or isinstance(right, BoolType):
            return BOOL
        # Resolve int literals to match concrete int type on the other side
        if isinstance(left, IntLiteralType):
            if isinstance(right, (FixedIntType, BigIntType)):
                left = right
            elif isinstance(right, IntLiteralType):
                return self.ctx.default_int_type
            else:
                return BOOL
        elif isinstance(right, IntLiteralType):
            if isinstance(left, (FixedIntType, BigIntType)):
                right = left
            else:
                return BOOL
        # Normalize PendingViewType to its owned type for comparison; preserve
        # the pending type when both sides are pending so view deduction can
        # chain the result variable back to the operands' resolution.
        if isinstance(left, PendingViewType) and isinstance(right, PendingViewType) and left.family is right.family:
            return left
        if isinstance(left, PendingViewType):
            left = left.family.owned_type
        if isinstance(right, PendingViewType):
            right = right.family.owned_type
        # Normalize pending container types to concrete types for equality comparison.
        # Two PendingListType literals with the same element type (but different IDs
        # or different IntLiteralType values like 1 vs 3) are compatible.
        # Caller marks source literals via collect_pending_source_types.
        left = self._normalize_pending_container(left)
        right = self._normalize_pending_container(right)
        if left == right:
            return left
        return BOOL

    def _analyze_binop(self, expr: TpyBinOp) -> TpyType:
        """Analyze a binary operation."""
        left_type = self.analyze_expr(expr.left)
        if expr.op in ("&&", "||"):
            type_true, type_false = self.narrowing.condition_type_facts(expr.left)
            saved_types = dict(self.ctx.narrowed_types)
            # Save definitely_assigned: RHS may not execute due to short-circuit
            saved_assigned = frozenset(self.ctx.definitely_assigned)
            if expr.op == "&&":
                self.ctx.narrowed_types.update(type_true)
            else:
                self.ctx.narrowed_types.update(type_false)
            try:
                right_type = self.analyze_expr(expr.right)
            finally:
                self.ctx.narrowed_types = saved_types
                # Track walrus vars introduced in RHS (short-circuit conditional)
                rhs_walrus = self.ctx.definitely_assigned - saved_assigned
                if rhs_walrus:
                    if expr.op == "&&":
                        self.ctx.sc_and_walrus |= rhs_walrus
                    else:
                        self.ctx.sc_or_walrus |= rhs_walrus
                # Rollback: RHS walrus vars are not definitely assigned
                self.ctx.definitely_assigned = set(saved_assigned)
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
            return isinstance(t, (Int32Type, BigIntType, IntLiteralType, FloatType, Float32Type, FloatLiteralType))

        # Identity operators (is / is not) -- only valid with None or enums
        if expr.op in ("is", "is not"):
            # Unwrap ReadonlyType and OwnType for nullable checks.
            left_check = unwrap_readonly(left_type)
            if isinstance(left_check, OwnType):
                left_check = left_check.wrapped
            right_check = unwrap_readonly(right_type)
            if isinstance(right_check, OwnType):
                right_check = right_check.wrapped
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
            # Validate that user record types support the comparison
            self._validate_comparison(expr, left_effective, right_effective)
            return BOOL

        # Membership operators (in, not in) return Bool
        if expr.op in ("in", "not in"):
            # Try __contains__ method (O(1) for dict, set, dict_keys; user-defined for records)
            right_record = self.ctx.registry.get_record_for_type(right_type)
            if right_record:
                contains_overloads = right_record.get_method_overloads("__contains__")
                if contains_overloads:
                    method = contains_overloads[0]
                    # Substitute type params for generic containers
                    from .operators import _substitute_type_params
                    type_subst = builtin_modules.extract_type_params(right_type)
                    param_type = unwrap_ref_type(method.params[0].type)
                    if type_subst:
                        param_type = _substitute_type_params(param_type, type_subst)
                    # Resolve IntLiteralType: use param type if the literal fits,
                    # otherwise fall back to default int type
                    check_left = left_type
                    if isinstance(check_left, IntLiteralType):
                        if (isinstance(param_type, FixedIntType)
                                and param_type.min_value <= check_left.value <= param_type.max_value):
                            check_left = param_type
                        else:
                            check_left = self.ctx.default_int_for_literal(check_left)
                    if not isinstance(param_type, TypeParamRef):
                        self.compat.check_type_compatible(
                            check_left, param_type,
                            f"membership test (expected {param_type})",
                            loc=expr.loc,
                        )
                    expr.resolved_contains = method
                    return BOOL
            # Right side must be iterable (intrinsically or via NativeIterable protocol)
            helper = IterableHelper(self.ctx)
            if helper.is_type_iterable(right_type):
                # For string containers, LHS must be str or Char
                if is_any_str_type(right_type):
                    if not (is_any_str_type(left_type) or isinstance(left_type, CharType)):
                        raise SemanticError(
                            f"Cannot check '{left_type}' membership in str (expected str or Char)",
                            expr.loc
                        )
                else:
                    # Non-string collections use std::find which requires ==
                    elem_type = right_type.get_element_type()
                    if elem_type is not None:
                        equatable = NamedType("Equatable", is_protocol=True)
                        if not self.protocols.type_conforms_to_protocol(elem_type, equatable):
                            raise self.ctx.error(
                                f"'in' requires element type '{elem_type}' to "
                                f"conform to 'Equatable' (no '__eq__' method)",
                                expr)
                return BOOL
            raise self.ctx.error(f"Cannot use '{expr.op}' with non-iterable type {right_type}", expr)

        # Logical operators: Python semantics returns an operand, not bool.
        # Same non-bool type -> return that type; otherwise -> bool.
        if expr.op in ("&&", "||"):
            result = self._logical_op_result_type(left_type, right_type)
            if isinstance(result, ListType):
                # Both ternary branches must share a C++ type; force sources to
                # ListType so they don't independently become incompatible Arrays.
                for t in collect_pending_source_types(self.ctx, expr):
                    if isinstance(t, PendingListType):
                        info = self.ctx.list_literals.get(t.literal_id)
                        if info is not None:
                            info.needs_list_type = True
            return result

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
            # Check if divisor is provably non-zero for div/mod elision
            if expr.op in ("//", "%"):
                self._check_divisor_non_zero(expr)
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
                            if not cpp and not method.native_function:
                                from .operators import DUNDER_CPP_TEMPLATES
                                cpp = DUNDER_CPP_TEMPLATES.get(method_name)
                            resolved_method = FunctionInfo(
                                name=method_name,
                                params=[ParamInfo(n, t) for n, t in method.params],
                                return_type=ret_type,
                                cpp_template=cpp,
                                native_name=method.native_name,
                                native_function=method.native_function,
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

    def _check_subscript_bounds_safe(self, expr: TpySubscript) -> None:
        """Set bounds_safe when the index is provably in [0, len(obj))."""
        index = expr.index
        obj = expr.obj
        if not isinstance(index, TpyName) or not isinstance(obj, TpyName):
            return
        index_range = self.ctx.value_ranges.get(index.name)
        is_safe = (
            index_range is not None
            and index_range.is_non_negative()
            and index_range.is_bounded_by_len(obj.name)
        )
        expr.bounds_safe = is_safe
        if expr.loc:
            self.ctx.subscript_bounds_facts[(expr.loc.line, obj.name)] = is_safe

    def _check_divisor_non_zero(self, expr: TpyBinOp) -> None:
        """Set divisor_non_zero when the divisor is provably != 0."""
        right = expr.right
        if isinstance(right, TpyIntLiteral):
            is_safe = right.value != 0
            expr.divisor_non_zero = is_safe
            return
        if not isinstance(right, TpyName):
            return
        divisor_range = self.ctx.value_ranges.get(right.name)
        is_safe = divisor_range is not None and divisor_range.non_zero
        expr.divisor_non_zero = is_safe
        if expr.loc:
            self.ctx.div_zero_facts[(expr.loc.line, right.name)] = is_safe

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

    def _analyze_chained_compare(self, expr: TpyChainedCompare) -> TpyType:
        """Analyze a chained comparison (a < b < c, etc.)."""
        pairs: list[TpyBinOp] = []
        prev = expr.left
        for op, comp in zip(expr.ops, expr.comparators):
            pair = TpyBinOp(prev, op, comp, loc=expr.loc)
            self.analyze_expr(pair)
            pairs.append(pair)
            prev = comp
        expr.pairs = pairs
        return BOOL

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
            if isinstance(effective_type, (BoolType, Int32Type, BigIntType, FloatType, Float32Type, IntLiteralType, FloatLiteralType, OptionalType, EnumType)):
                return BOOL
            record = self.ctx.registry.get_record_for_type(effective_type)
            if record and (record.get_method_overloads("__bool__")
                           or record.get_method_overloads("__len__")):
                return BOOL
            raise self.ctx.error(f"Invalid operand type for 'not': {operand_type} (expected bool, numeric, or type with __bool__/__len__)", expr)

        # Float types support unary negation and plus
        if isinstance(effective_type, (FloatType, Float32Type, FloatLiteralType)):
            if expr.op in ("-", "+"):
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
            if expr.op == "+":
                if result := self.operators.resolve_unaryop(effective_type, expr.op):
                    expr.resolved_unaryop = result
                return effective_type
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

    def _is_user_record_type(self, typ: TpyType) -> bool:
        """Check if a type is a user-defined record (not a builtin container)."""
        return isinstance(typ, NamedType) and typ.is_record and typ.is_user_record

    def _validate_comparison(self, expr: TpyBinOp, left_type: TpyType, right_type: TpyType) -> None:
        """Error when comparing user record types that lack the relevant dunder."""
        # Only check when at least one side is a user record
        if not self._is_user_record_type(left_type) and not self._is_user_record_type(right_type):
            return
        # Determine which dunder to check
        COMPARE_OP_TO_DUNDER = {
            "==": "__eq__", "!=": "__ne__",
            "<": "__lt__", "<=": "__le__",
            ">": "__gt__", ">=": "__ge__",
        }
        dunder = COMPARE_OP_TO_DUNDER.get(expr.op)
        if not dunder:
            return
        # Check the left side (operator dispatch goes left to right)
        check_type = left_type if self._is_user_record_type(left_type) else right_type
        record = self.ctx.registry.get_record_for_type(check_type)
        if record:
            # Use lookup that walks the inheritance chain
            overloads, _ = self.protocols.lookup_record_method_overloads(record, dunder)
            has_dunder = bool(overloads)
            # != is valid if __eq__ is defined (C++ generates != from ==)
            if not has_dunder and dunder == "__ne__":
                overloads, _ = self.protocols.lookup_record_method_overloads(record, "__eq__")
                has_dunder = bool(overloads)
            # For ordering, check if dataclass(order=True)
            if not has_dunder and dunder in ("__lt__", "__le__", "__gt__", "__ge__"):
                record_info = self.ctx.registry.get_record(check_type.name)
                if record_info and record_info.is_ordered:
                    has_dunder = True
            if not has_dunder:
                if dunder == "__ne__":
                    msg = (f"Comparison '!=' on '{check_type}': "
                           f"no '__ne__' or '__eq__' method defined")
                else:
                    msg = (f"Comparison '{expr.op}' on '{check_type}': "
                           f"no '{dunder}' method defined")
                self.ctx.emit_error(msg, expr)

    def get_deref_target_type(self, typ: TpyType, is_readonly: bool = False) -> TpyType | None:
        """If typ has __deref__(), return resolved return type. Else None."""
        return self.type_ops.get_deref_target_type(typ, is_readonly=is_readonly)

    def _try_find_field(self, typ: TpyType, expr: TpyFieldAccess) -> TpyType | None:
        """Try to find a field on typ. Returns field type or None."""
        if isinstance(typ, SliceType):
            if expr.field in ("start", "stop"):
                return OptionalType(INT32)
            return None

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
        actual_type = unwrap_ref_type(obj_type)
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

        # Pending generic instance: field access is not allowed until resolved
        if isinstance(actual_type, PendingGenericInstanceType):
            raise self.ctx.error(
                f"Cannot access field '{expr.field}' on '{actual_type.record_name}' "
                f"until its type arguments are resolved; call a constraining method first "
                f"or add explicit type arguments to the constructor",
                expr,
            )

        # Enum instance property access: c.name -> str, c.value -> underlying type
        if isinstance(actual_type, EnumType):
            if expr.field == "name":
                return STR
            elif expr.field == "value":
                return actual_type.underlying_type
            raise self.ctx.error(
                f"Enum value of type '{actual_type.name}' has no attribute '{expr.field}'. "
                f"Use '{actual_type.name}.{expr.field}' to access enum members", expr)

        # Deref chain loop -- resolves through Ptr (mutable and readonly) and any Deref[T] type
        current_type = actual_type
        deref_depth = 0
        while deref_depth <= 8:
            result = self._try_find_field(current_type, expr)
            if result is not None:
                expr.deref_depth = deref_depth
                if deref_depth > 0 and isinstance(actual_type, PtrType):
                    obj_key = _expr_to_narrowing_key(expr.obj)
                    if obj_key is not None:
                        if obj_key in self.ctx.non_null_ptr_vars:
                            expr.ptr_non_null = True
                        if expr.loc:
                            self.ctx.ptr_deref_facts[
                                (expr.loc.line, obj_key)
                            ] = expr.ptr_non_null
                # Propagate readonly: accessing a non-value field through a
                # readonly reference yields a readonly result.
                # Ptr[T] fields become Ptr[readonly[T]], Span[T] -> Span[readonly[T]].
                if is_readonly_obj:
                    if isinstance(result, PtrType) and not result.is_readonly:
                        result = result.as_const()
                    elif isinstance(result, SpanType) and not result.is_readonly:
                        result = result.as_const()
                    elif not result.is_value_type():
                        result = ReadonlyType(unwrap_readonly(result))
                # Ownership propagation: in a consuming method (self: Own[Self]),
                # self.field yields Own[FieldType] since the struct is being consumed.
                if (self.ctx.in_consuming_method
                        and not is_readonly_obj
                        and isinstance(expr.obj, TpyName) and expr.obj.name == "self"
                        and not result.is_value_type()):
                    result = OwnType(result)
                # Apply field path narrowing (e.g. after `if obj.field is not None:`)
                field_key = _expr_to_narrowing_key(expr)
                if field_key is not None:
                    narrowed = self.ctx.narrowed_types.get(field_key)
                    if narrowed is not None:
                        result = narrowed
                return result

            deref_target = self.get_deref_target_type(
                current_type, is_readonly=is_readonly_obj)
            if deref_target is None:
                break
            # __deref__() may return readonly[T]; unwrap and propagate
            # readonly so field access enforces const semantics.
            if isinstance(deref_target, ReadonlyType):
                is_readonly_obj = True
                deref_target = deref_target.wrapped
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
            if not isinstance(self.ctx.current_function, TpyFunction):
                raise self.ctx.error("Empty array literal requires explicit type annotation", expr)
            # Empty list with no annotation -- create PendingListType with unknown
            # element type. The element type will be inferred from subsequent usage
            # (e.g. .append(v), xs[i] = v, param context, return context).

            literal_id = self.ctx.literal_counter
            self.ctx.literal_counter += 1
            info = ListLiteralInfo(
                literal_id=literal_id,
                expr=expr,
                element_type=UNKNOWN_ELEMENT,
                size=0,
                is_mutated=True,  # empty list is always list, never Array
            )
            self.ctx.list_literals[literal_id] = info
            self.ctx.pending_resolutions.append(literal_id)
            return PendingListType(UNKNOWN_ELEMENT, 0, literal_id)

        # Analyze all elements, propagating expected type as hint when available
        if expected_elem is not None:
            elem_types = [self.analyze_expr_with_hint(e, expected_elem) for e in expr.elements]
        else:
            elem_types = [self.analyze_expr(e) for e in expr.elements]

        if expected_elem is not None:
            # Contextual mode: check each element against expected element type
            first_type = expected_elem
            for i, elem_type in enumerate(elem_types, 1):
                if elem_type == expected_elem:
                    continue
                # Subclass coercion excluded: storing Child in list[Base] silently
                # slices objects (same invariance as dict/set).
                if (isinstance(elem_type, NamedType) and elem_type.is_user_record
                        and isinstance(expected_elem, NamedType) and expected_elem.is_user_record):
                    raise self.ctx.error(
                        f"List literal element {i} has type {elem_type}, "
                        f"incompatible with annotated element type {expected_elem}", expr
                    )
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
                # FloatLiteralType elements are compatible with each other
                if isinstance(first_type, FloatLiteralType) and isinstance(elem_type, FloatLiteralType):
                    continue
                # FloatLiteral coerces to concrete float types
                if isinstance(elem_type, FloatLiteralType) and isinstance(first_type, (FloatType, Float32Type)):
                    continue
                if isinstance(first_type, FloatLiteralType) and isinstance(elem_type, (FloatType, Float32Type)):
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
                # Tuples with same structure but different IntLiteralType values
                if (isinstance(first_type, TupleType) and isinstance(elem_type, TupleType)
                        and len(first_type.element_types) == len(elem_type.element_types)
                        and all(
                            a == b
                            or (isinstance(a, IntLiteralType) and isinstance(b, IntLiteralType))
                            or (isinstance(a, FloatLiteralType) and isinstance(b, FloatLiteralType))
                            for a, b in zip(first_type.element_types, elem_type.element_types))):
                    continue
                if elem_type != first_type:
                    ft = self._user_type_name(first_type)
                    et = self._user_type_name(elem_type)
                    raise self.ctx.error(
                        f"List literal has mixed types: element {i} is {et}, "
                        f"but earlier elements are {ft}. "
                        f"Use a type annotation like list[{ft} | {et}]", expr
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
        if isinstance(key_type, PendingViewType):
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

    def _analyze_named_expr(self, expr: TpyNamedExpr) -> TpyType:
        """Analyze walrus operator: (x := expr)."""
        value_type = self.analyze_expr(expr.value)
        name = expr.target

        # Resolve pending/literal types for the variable binding
        resolved = value_type
        if isinstance(resolved, IntLiteralType):
            resolved = self.ctx.default_int_type
        elif isinstance(resolved, FloatLiteralType):
            resolved = FLOAT
        elif isinstance(resolved, PendingViewType):
            resolved = resolved.family.owned_type

        # PEP 572: walrus in comprehension leaks to enclosing function scope
        target_scope = self.ctx.current_scope
        levels = self.ctx.in_comprehension
        while levels > 0 and target_scope.parent is not None:
            target_scope = target_scope.parent
            levels -= 1

        existing = target_scope.lookup(name)
        if existing is not None:
            # Reassignment via walrus -- keep existing type
            pass
        else:
            # New binding
            target_scope.define(name, resolved)
            if self.ctx.current_ns:
                self.ctx.current_ns.bind_variable(name, resolved)

        self.ctx.definitely_assigned.add(name)
        self.ctx.rvalue_vars.add(name)
        if name not in self.ctx.var_scope_depth:
            self.ctx.var_scope_depth[name] = target_scope.depth

        return value_type

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

        if isinstance(common, ListType):
            # Both ternary branches must share a C++ type; force sources to
            # ListType so they don't independently become incompatible Arrays.
            for t in collect_pending_source_types(self.ctx, expr):
                if isinstance(t, PendingListType):
                    info = self.ctx.list_literals.get(t.literal_id)
                    if info is not None:
                        info.needs_list_type = True

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

        # FloatLiteral resolution: adapts to the concrete float type in context
        if isinstance(t, FloatLiteralType) and isinstance(e, FloatLiteralType):
            return FLOAT
        if isinstance(t, FloatLiteralType):
            if isinstance(e, (FloatType, Float32Type)):
                return e
            t = FLOAT
        if isinstance(e, FloatLiteralType):
            if isinstance(t, (FloatType, Float32Type)):
                return t
            e = FLOAT

        # Normalize PendingViewType to its owned type for comparison; preserve
        # the pending type when both sides are pending so view deduction can
        # chain the result variable back to the operands' resolution.
        if isinstance(t, PendingViewType) and isinstance(e, PendingViewType) and t.family is e.family:
            return t
        if isinstance(t, PendingViewType):
            t = t.family.owned_type
        if isinstance(e, PendingViewType):
            e = e.family.owned_type
        # Normalize pending container types to concrete types for equality comparison,
        # resolving IntLiteralType elements so [1,2] and [3,4] both normalize to list[int].
        t = self._normalize_pending_container(t)
        e = self._normalize_pending_container(e)

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
            if not isinstance(self.ctx.current_function, TpyFunction):
                raise self.ctx.error(
                    "Empty dict literal requires explicit type annotation", expr)
            literal_id = self.ctx.literal_counter
            self.ctx.literal_counter += 1
            info = DictLiteralInfo(
                literal_id=literal_id,
                expr=expr,
                key_type=UNKNOWN_ELEMENT,
                value_type=UNKNOWN_ELEMENT,
            )
            self.ctx.dict_literals[literal_id] = info
            self.ctx.pending_dict_resolutions.append(literal_id)
            return PendingDictType(UNKNOWN_ELEMENT, UNKNOWN_ELEMENT, literal_id)

        if expected_key:
            key_types = [self.analyze_expr_with_hint(k, expected_key) for k in expr.keys]
        else:
            key_types = [self.analyze_expr(k) for k in expr.keys]
        if expected_value:
            value_types = [self.analyze_expr_with_hint(v, expected_value) for v in expr.values]
        else:
            value_types = [self.analyze_expr(v) for v in expr.values]

        # Unify key types
        if isinstance(expected_key, (UnionType, OptionalType)):
            key_type = expected_key
            for i, kt in enumerate(key_types, 1):
                if kt == expected_key:
                    continue
                try:
                    self.compat.check_type_compatible(
                        kt, expected_key, f"dict literal key {i}", expr.loc)
                except SemanticError:
                    raise self.ctx.error(
                        f"Dict literal key {i} has type {kt}, "
                        f"incompatible with annotated key type {expected_key}", expr)
        else:
            key_type = key_types[0]
            for i, kt in enumerate(key_types[1:], 2):
                if isinstance(key_type, IntLiteralType) and isinstance(kt, IntLiteralType):
                    continue
                if isinstance(kt, IntLiteralType) and isinstance(key_type, (Int32Type, BigIntType)):
                    continue
                if isinstance(key_type, IntLiteralType) and isinstance(kt, (Int32Type, BigIntType)):
                    key_type = kt
                    continue
                if isinstance(key_type, FloatLiteralType) and isinstance(kt, FloatLiteralType):
                    continue
                if isinstance(kt, FloatLiteralType) and isinstance(key_type, (FloatType, Float32Type)):
                    continue
                if isinstance(key_type, FloatLiteralType) and isinstance(kt, (FloatType, Float32Type)):
                    key_type = kt
                    continue
                if kt != key_type:
                    raise self.ctx.error(
                        f"Dict has mixed key types: key {i} is {self._user_type_name(kt)}, "
                        f"but earlier keys are {self._user_type_name(key_type)}", expr,
                    )

        # Unify value types
        if isinstance(expected_value, (UnionType, OptionalType)):
            # Annotation provides a union/optional -- validate each value against it
            value_type = expected_value
            for i, vt in enumerate(value_types, 1):
                if vt == expected_value:
                    continue
                try:
                    self.compat.check_type_compatible(
                        vt, expected_value, f"dict literal value {i}", expr.loc)
                except SemanticError:
                    raise self.ctx.error(
                        f"Dict literal value {i} has type {vt}, "
                        f"incompatible with annotated value type {expected_value}", expr)
        else:
            value_type = value_types[0]
            for i, vt in enumerate(value_types[1:], 2):
                if isinstance(value_type, IntLiteralType) and isinstance(vt, IntLiteralType):
                    continue
                if isinstance(vt, IntLiteralType) and isinstance(value_type, (Int32Type, BigIntType)):
                    continue
                if isinstance(value_type, IntLiteralType) and isinstance(vt, (Int32Type, BigIntType)):
                    value_type = vt
                    continue
                if isinstance(value_type, FloatLiteralType) and isinstance(vt, FloatLiteralType):
                    continue
                if isinstance(vt, FloatLiteralType) and isinstance(value_type, (FloatType, Float32Type)):
                    continue
                if isinstance(value_type, FloatLiteralType) and isinstance(vt, (FloatType, Float32Type)):
                    value_type = vt
                    continue
                if vt != value_type:
                    raise self.ctx.error(
                        f"Dict has mixed value types: value {i} is {self._user_type_name(vt)}, "
                        f"but earlier values are {self._user_type_name(value_type)}", expr,
                    )

        # Use annotation types when literal elements are IntLiteralType or FloatLiteralType
        if isinstance(key_type, IntLiteralType):
            key_type = expected_key if expected_key else self.ctx.default_int_for_literal(key_type)
        if isinstance(value_type, IntLiteralType):
            value_type = expected_value if expected_value else self.ctx.default_int_for_literal(value_type)
        if isinstance(key_type, FloatLiteralType):
            key_type = expected_key if isinstance(expected_key, (FloatType, Float32Type)) else FLOAT
        if isinstance(value_type, FloatLiteralType):
            value_type = expected_value if isinstance(expected_value, (FloatType, Float32Type)) else FLOAT
        # Container elements must be owned -- views can't be stored in a dict.
        if isinstance(key_type, PendingViewType):
            key_type = key_type.family.owned_type
        if isinstance(value_type, PendingViewType):
            value_type = value_type.family.owned_type

        self._validate_dict_key_type(key_type, expr)
        return DictType(key_type, value_type)

    def _analyze_set_literal(
        self, expr: TpySetLiteral,
        expected_elem: TpyType | None = None,
    ) -> TpyType:
        """Analyze a set literal {value, ...}"""
        if not expr.elements:
            raise self.ctx.error(
                "Empty set literal requires type annotation "
                "(e.g. s: set[int] = set())", expr,
            )

        if expected_elem:
            elem_types = [self.analyze_expr_with_hint(e, expected_elem) for e in expr.elements]
        else:
            elem_types = [self.analyze_expr(e) for e in expr.elements]

        # Unify element types
        if isinstance(expected_elem, (UnionType, OptionalType)):
            elem_type = expected_elem
            for i, et in enumerate(elem_types, 1):
                if et == expected_elem:
                    continue
                try:
                    self.compat.check_type_compatible(
                        et, expected_elem, f"set literal element {i}", expr.loc)
                except SemanticError:
                    raise self.ctx.error(
                        f"Set literal element {i} has type {et}, "
                        f"incompatible with annotated element type {expected_elem}", expr,
                    )
        else:
            elem_type = elem_types[0]
            for i, et in enumerate(elem_types[1:], 2):
                if isinstance(elem_type, IntLiteralType) and isinstance(et, IntLiteralType):
                    continue
                if isinstance(et, IntLiteralType) and isinstance(elem_type, (Int32Type, BigIntType)):
                    continue
                if isinstance(elem_type, IntLiteralType) and isinstance(et, (Int32Type, BigIntType)):
                    elem_type = et
                    continue
                if isinstance(elem_type, FloatLiteralType) and isinstance(et, FloatLiteralType):
                    continue
                if isinstance(et, FloatLiteralType) and isinstance(elem_type, (FloatType, Float32Type)):
                    continue
                if isinstance(elem_type, FloatLiteralType) and isinstance(et, (FloatType, Float32Type)):
                    elem_type = et
                    continue
                if et != elem_type:
                    raise self.ctx.error(
                        f"Set has mixed element types: element {i} is {self._user_type_name(et)}, "
                        f"but earlier elements are {self._user_type_name(elem_type)}", expr,
                    )

        if isinstance(elem_type, IntLiteralType):
            elem_type = expected_elem if expected_elem else self.ctx.default_int_for_literal(elem_type)
        if isinstance(elem_type, FloatLiteralType):
            elem_type = expected_elem if isinstance(expected_elem, (FloatType, Float32Type)) else FLOAT
        # Container elements must be owned -- views can't be stored in a set.
        if isinstance(elem_type, PendingViewType):
            elem_type = elem_type.family.owned_type

        self._validate_dict_key_type(elem_type, expr)
        return SetType(elem_type)

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
            if isinstance(first_type, FloatLiteralType) and isinstance(elem_type, FloatLiteralType):
                continue
            if isinstance(elem_type, FloatLiteralType) and isinstance(first_type, (FloatType, Float32Type)):
                continue
            if isinstance(first_type, FloatLiteralType) and isinstance(elem_type, (FloatType, Float32Type)):
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

    def _analyze_list_comprehension(
        self, expr: TpyListComprehension, expected_elem: TpyType | None = None
    ) -> TpyType:
        return self._analyze_elem_comprehension(expr, expected_elem, kind="list")

    def _analyze_set_comprehension(
        self, expr: TpySetComprehension, expected_elem: TpyType | None = None
    ) -> TpyType:
        return self._analyze_elem_comprehension(expr, expected_elem, kind="set")

    def _analyze_generator_expression(self, expr: TpyGeneratorExpression) -> TpyType:
        gen = expr.generator
        elem_type = self._resolve_comp_iterable(gen, expr)

        if self.scopes is None:
            raise RuntimeError("generator expression requires ScopeTracker")
        result_elem_type = self._enter_comp_scope(gen, expr, elem_type, None)

        if isinstance(result_elem_type, IntLiteralType):
            result_elem_type = self.ctx.default_int_type
        elif isinstance(result_elem_type, FloatLiteralType):
            result_elem_type = FLOAT
        else:
            result_elem_type = resolve_int_literals(result_elem_type, self.ctx.default_int_for_literal)

        expr.result_elem_type = result_elem_type
        return GenExprType(result_elem_type)

    def _analyze_elem_comprehension(
        self, expr: TpyListComprehension | TpySetComprehension,
        expected_elem: TpyType | None,
        kind: Literal["list", "set"],
    ) -> TpyType:
        """Shared analysis for list and set comprehensions."""
        gen = expr.generator
        elem_type = self._resolve_comp_iterable(gen, expr)

        if self.scopes is None:
            raise RuntimeError(f"{kind} comprehension requires ScopeTracker")
        result_elem_type = self._enter_comp_scope(gen, expr, elem_type, expected_elem)

        if isinstance(result_elem_type, IntLiteralType):
            result_elem_type = expected_elem if expected_elem is not None else self.ctx.default_int_type
        if isinstance(result_elem_type, FloatLiteralType):
            result_elem_type = expected_elem if isinstance(expected_elem, (FloatType, Float32Type)) else FLOAT

        if expected_elem is not None and result_elem_type != expected_elem:
            # Subclass coercion excluded: storing Child in list/set[Base] silently
            # slices objects (same invariance as container literals).
            if (isinstance(result_elem_type, NamedType) and result_elem_type.is_user_record
                    and isinstance(expected_elem, NamedType) and expected_elem.is_user_record):
                raise self.ctx.error(
                    f"{kind.capitalize()} comprehension element has type {result_elem_type}, "
                    f"incompatible with annotated element type {expected_elem}", expr
                )
            expr.element_expr = self.compat.coerce_expr(
                expr.element_expr, result_elem_type, expected_elem,
                f"{kind} comprehension element", coercion_ctx=CoercionContext.INIT)
            result_elem_type = expected_elem

        if kind == "set":
            self._validate_dict_key_type(result_elem_type, expr)

        expr.result_elem_type = result_elem_type

        if kind == "list":
            array_size = self._try_comp_array_size(expr)
            if array_size is not None and self.ctx.current_function is not None:
                literal_id = self.ctx.literal_counter
                self.ctx.literal_counter += 1
                info = ListLiteralInfo(
                    literal_id=literal_id,
                    expr=expr,
                    element_type=result_elem_type,
                    size=array_size,
                    is_global=self.ctx.is_top_level,
                )
                self.ctx.list_literals[literal_id] = info
                self.ctx.pending_resolutions.append(literal_id)
                return PendingListType(result_elem_type, array_size, literal_id)

        return SetType(result_elem_type) if kind == "set" else ListType(result_elem_type)

    def _try_comp_array_size(self, expr: TpyListComprehension) -> int | None:
        """Return the compile-time known size if this comprehension can be an Array."""
        gen = expr.generator
        if gen.conditions:
            return None

        # range(N) or range(start, stop) with literal args
        if isinstance(gen.iterable, TpyCall) and gen.iterable.func_name == "range":
            return self._range_literal_size(gen.iterable)

        # Array[T, N] source -- size is known from the type
        iterable_type = unwrap_readonly(self.ctx.get_expr_type(gen.iterable))
        if isinstance(iterable_type, ArrayType):
            return iterable_type.size

        return None

    @staticmethod
    def _try_int_literal(expr: TpyExpr) -> int | None:
        """Extract an integer literal value, unwrapping TpyCoerce and unary minus."""
        if isinstance(expr, TpyCoerce):
            expr = expr.expr
        if isinstance(expr, TpyIntLiteral):
            return expr.value
        if isinstance(expr, TpyUnaryOp) and expr.op == '-' and isinstance(expr.operand, TpyIntLiteral):
            return -expr.operand.value
        return None

    def _range_literal_size(self, call: TpyCall) -> int | None:
        """Extract compile-time size from range() with literal args."""
        args = call.args
        if len(args) == 1:
            n = self._try_int_literal(args[0])
            if n is not None:
                return max(n, 0)
        elif len(args) == 2:
            s = self._try_int_literal(args[0])
            e = self._try_int_literal(args[1])
            if s is not None and e is not None and e >= s:
                return e - s
        elif len(args) == 3:
            s = self._try_int_literal(args[0])
            e = self._try_int_literal(args[1])
            d = self._try_int_literal(args[2])
            if s is not None and e is not None and d is not None and d != 0:
                if d > 0 and e > s:
                    return (e - s + d - 1) // d
                elif d < 0 and s > e:
                    return (s - e - d - 1) // (-d)
                else:
                    return 0
        return None

    def _analyze_dict_comprehension(
        self, expr: TpyDictComprehension,
        expected_key: TpyType | None = None,
        expected_value: TpyType | None = None,
    ) -> TpyType:
        """Analyze a dict comprehension: {key: value for var in iterable if cond}"""
        gen = expr.generator
        elem_type = self._resolve_comp_iterable(gen, expr)

        if self.scopes is None:
            raise RuntimeError("dict comprehension requires ScopeTracker")
        key_type, value_type = self._enter_comp_scope(
            gen, expr, elem_type, (expected_key, expected_value))

        if isinstance(key_type, IntLiteralType):
            key_type = expected_key if expected_key is not None else self.ctx.default_int_type
        if isinstance(value_type, IntLiteralType):
            value_type = expected_value if expected_value is not None else self.ctx.default_int_type
        if isinstance(key_type, FloatLiteralType):
            key_type = expected_key if isinstance(expected_key, (FloatType, Float32Type)) else FLOAT
        if isinstance(value_type, FloatLiteralType):
            value_type = expected_value if isinstance(expected_value, (FloatType, Float32Type)) else FLOAT

        if expected_key is not None and key_type != expected_key:
            expr.key_expr = self.compat.coerce_expr(
                expr.key_expr, key_type, expected_key,
                "dict comprehension key", coercion_ctx=CoercionContext.INIT)
            key_type = expected_key
        if expected_value is not None and value_type != expected_value:
            expr.value_expr = self.compat.coerce_expr(
                expr.value_expr, value_type, expected_value,
                "dict comprehension value", coercion_ctx=CoercionContext.INIT)
            value_type = expected_value

        self._validate_dict_key_type(key_type, expr)
        expr.result_key_type = key_type
        expr.result_value_type = value_type
        return DictType(key_type, value_type)

    def _resolve_comp_iterable(
        self, gen: TpyComprehensionGenerator, expr: TpyExpr
    ) -> TpyType:
        """Analyze the iterable and extract its element type (shared by all comprehensions)."""
        iterable_type = self.analyze_expr(gen.iterable)
        inner = unwrap_readonly(iterable_type)
        if isinstance(inner, TypeParamRef):
            bound = self.type_ops.get_type_param_bound(inner.name)
            if bound is not None and is_protocol_type(bound):
                inner = bound
        return IterableHelper(self.ctx).get_iterable_element_type(inner, loc=expr.loc)

    def _enter_comp_scope(
        self,
        gen: TpyComprehensionGenerator,
        expr: TpyListComprehension | TpySetComprehension | TpyDictComprehension | TpyGeneratorExpression,
        elem_type: TpyType,
        hint: TpyType | tuple[TpyType | None, TpyType | None] | None,
    ) -> TpyType | tuple[TpyType, TpyType]:
        # Comprehension loop vars are never mutated -- use const ref for
        # non-value types and expensive-to-copy value types (str, BigInt).
        unwrapped = unwrap_readonly(elem_type)
        if not unwrapped.is_value_type() or unwrapped.is_expensive_copy():
            gen.const_loop_var = True
        with self.scopes.comprehension_scope() as inner_scope:
            if gen.unpack_vars is not None:
                if not isinstance(elem_type, TupleType):
                    raise self.ctx.error(
                        f"Cannot unpack non-tuple type {elem_type}", expr)
                if len(gen.unpack_vars) != len(elem_type.element_types):
                    raise self.ctx.error(
                        f"Cannot unpack tuple of {len(elem_type.element_types)} "
                        f"elements into {len(gen.unpack_vars)} targets", expr)
                with ExitStack() as stack:
                    for uvar, utype in zip(gen.unpack_vars, elem_type.element_types):
                        if uvar is not None:
                            stack.enter_context(
                                self.scopes.loop_var(inner_scope, uvar, utype,
                                                     inner_scope.depth, is_foreach=False))
                    return self._analyze_comp_body(gen, expr, hint)
            else:
                with self.scopes.loop_var(inner_scope, gen.var, elem_type,
                                          inner_scope.depth, is_foreach=False):
                    return self._analyze_comp_body(gen, expr, hint)

    def _analyze_comp_body(
        self,
        gen: TpyComprehensionGenerator,
        expr: TpyListComprehension | TpySetComprehension | TpyDictComprehension | TpyGeneratorExpression,
        hint: TpyType | tuple[TpyType | None, TpyType | None] | None,
    ) -> TpyType | tuple[TpyType, TpyType]:
        for cond in gen.conditions:
            self.analyze_expr(cond)
        if isinstance(expr, TpyDictComprehension):
            key_hint, value_hint = hint
            key_type = (self.analyze_expr_with_hint(expr.key_expr, key_hint)
                        if key_hint else self.analyze_expr(expr.key_expr))
            value_type = (self.analyze_expr_with_hint(expr.value_expr, value_hint)
                          if value_hint else self.analyze_expr(expr.value_expr))
            return key_type, value_type
        if hint is not None:
            return self.analyze_expr_with_hint(expr.element_expr, hint)
        return self.analyze_expr(expr.element_expr)

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

        # Unwrap Own[T] -- ownership marker doesn't affect subscript behavior
        inner_obj_type = obj_type.wrapped if isinstance(obj_type, OwnType) else obj_type

        # Tuple indexing: t[0], t[-1] -- compile-time constant index only
        actual_for_tuple = unwrap_readonly(inner_obj_type)
        if isinstance(actual_for_tuple, TupleType):
            return self._analyze_tuple_subscript(expr, actual_for_tuple)

        # Slice: obj[start:stop]
        if isinstance(expr.index, TpySlice):
            return self._analyze_slice(expr, inner_obj_type)

        index_type = self.analyze_expr(expr.index)

        # Dict subscript: d[key] -> V (key can be non-integer)
        actual_obj = unwrap_readonly(inner_obj_type)
        if isinstance(actual_obj, PendingDictType):
            if not isinstance(actual_obj.key_type, UnknownElementType):
                self.compat.check_type_compatible(
                    index_type, actual_obj.key_type,
                    f"dict key (expected {actual_obj.key_type})",
                    loc=expr.loc,
                )
            return actual_obj.value_type
        if isinstance(actual_obj, DictType):
            self.compat.check_type_compatible(
                index_type, actual_obj.key_type,
                f"dict key (expected {actual_obj.key_type})",
                loc=expr.loc,
            )
            return actual_obj.value_type

        if not isinstance(index_type, (Int32Type, BigIntType, IntLiteralType)):
            raise self.ctx.error(f"Subscript index must be an integer type, got {index_type}", expr)

        # Check if index is provably in-bounds for bounds check elision
        self._check_subscript_bounds_safe(expr)

        # Unwrap ReadonlyType, remember the flag
        is_readonly_obj = isinstance(inner_obj_type, ReadonlyType)
        actual_type = unwrap_readonly(inner_obj_type)

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
    _SLICEABLE_BYTES_TYPES = (BytesType, ByteArrayType, BytesViewType, PendingBytesType)
    _SLICEABLE_CONTAINER_TYPES = (ListType, PendingListType, ArrayType, SpanType)

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
        is_readonly = isinstance(obj_type, ReadonlyType)
        actual_type = unwrap_readonly(obj_type)

        # String slicing -> StrView
        if isinstance(actual_type, self._SLICEABLE_STR_TYPES):
            return STRVIEW

        # Bytes slicing -> owned bytes (not a view -- avoids dangling span
        # since bytes literals are temporary vectors, not static storage)
        if isinstance(actual_type, self._SLICEABLE_BYTES_TYPES):
            return BYTES

        # Container slicing -> Span[T] or Span[readonly[T]]
        if isinstance(actual_type, self._SLICEABLE_CONTAINER_TYPES):
            elem_type = actual_type.get_element_type()
            assert elem_type is not None
            # Span[readonly[T]] source or @readonly context -> Span[readonly[T]]
            src_readonly = isinstance(actual_type, SpanType) and actual_type.is_readonly
            return SpanType(elem_type, is_readonly=(is_readonly or src_readonly))

        # User records with __getitem__(slice) overload
        if isinstance(actual_type, NamedType) and actual_type.is_record:
            ret = self._find_slice_getitem_return(actual_type, is_readonly=is_readonly)
            if ret is not None:
                expr.user_slice_getitem = True
                return ret

        raise self.ctx.error(f"Slicing is not supported for {obj_type}", expr)

    def _find_slice_getitem_return(self, record_type: NamedType, *, is_readonly: bool = False) -> TpyType | None:
        """Find __getitem__(slice) overload on a record and return its return type.

        Prefers the const overload when is_readonly=True (readonly receiver),
        and the mutable overload otherwise. Falls back to the first slice overload
        if no const/mutable-specific one exists.
        """
        record = self.ctx.registry.get_record_for_type(record_type)
        if record is None:
            return None
        getitem_overloads = record.methods.get("__getitem__", [])
        slice_overloads = [
            fi for fi in getitem_overloads
            if len(fi.params) == 1 and isinstance(fi.params[0].type, SliceType)
        ]
        if not slice_overloads:
            return None
        preferred = [fi for fi in slice_overloads if fi.is_readonly == is_readonly]
        func_info = preferred[0] if preferred else slice_overloads[0]
        ret = func_info.return_type
        type_subst = self.type_ops.build_type_substitution(record_type)
        if type_subst:
            ret = self.type_ops.substitute_type_params(ret, type_subst)
        return ret

    _FORMATTABLE_TYPES = (
        FixedIntType, BigIntType, IntLiteralType, FloatType, Float32Type, FloatLiteralType, BoolType,
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

    # --- Lambda expressions ---

    def _analyze_lambda(self, expr: TpyLambda) -> TpyType:
        """Analyze a lambda without a type hint -- error (types cannot be inferred)."""
        raise self.ctx.error(
            "Lambda parameter types cannot be inferred without context. "
            "Pass the lambda to a function that accepts Fn[...] or Callable[...] type",
            expr
        )

    def _analyze_lambda_with_fn_hint(self, expr: TpyLambda, fn_type: FnType | CallableType) -> FnType | CallableType:
        """Analyze a lambda with a Fn/Callable type hint providing parameter types."""
        type_name = "Fn" if isinstance(fn_type, FnType) else "Callable"
        if len(expr.param_names) != len(fn_type.param_types):
            raise self.ctx.error(
                f"Lambda has {len(expr.param_names)} parameter(s) but "
                f"{type_name} type expects {len(fn_type.param_types)}",
                expr
            )

        expr.inferred_param_types = list(fn_type.param_types)

        # Save outer scope locals for capture filtering
        outer_locals = set(self.ctx.definitely_assigned)

        with self.scopes.lambda_scope() as scope:
            for pname, ptype in zip(expr.param_names, fn_type.param_types):
                scope.define(pname, ptype)
                if self.ctx.current_ns:
                    self.ctx.current_ns.bind_variable(pname, ptype)
                self.ctx.definitely_assigned.add(pname)

            body_type = self.analyze_expr(expr.body)

        # Detect captures: names in body that are local variables from the outer scope
        # (not lambda params, not global functions, not builtins)
        param_set = set(expr.param_names)
        free_names = collect_name_refs(expr.body)
        captured = sorted((free_names - param_set) & outer_locals)
        expr.captured_names = captured
        # Callable context: captures must be by value (std::function can escape)
        if isinstance(fn_type, CallableType):
            expr.captures_by_value = True

        # Check return type compatibility (allow implicit coercions like int literal -> Int32)
        if isinstance(fn_type.return_type, TypeParamRef):
            # Hint has unresolved type param (e.g. from generic builtin map[T,U]):
            # use the body's inferred type and return a concrete FnType.
            # Resolve IntLiteralType so overload resolution sees a concrete int type.
            if isinstance(body_type, IntLiteralType):
                body_type = self.ctx.default_int_for_literal(body_type)
            expr.inferred_return_type = body_type
            concrete_params = tuple(fn_type.param_types)
            if isinstance(fn_type, CallableType):
                return CallableType(concrete_params, body_type)
            return FnType(concrete_params, body_type)
        if body_type != fn_type.return_type:
            try:
                self.compat.check_type_compatible(
                    body_type, fn_type.return_type,
                    "lambda return", loc=expr.loc)
            except SemanticError:
                raise self.ctx.error(
                    f"Lambda body type '{body_type}' is not compatible with "
                    f"expected return type '{fn_type.return_type}'",
                    expr
                )

        expr.inferred_return_type = fn_type.return_type
        return fn_type

    # --- Function references ---

    def _try_resolve_function_ref(
        self, expr: TpyName, hint: FnType | CallableType,
    ) -> FnType | CallableType | None:
        """Try to resolve a name as a function reference matching an Fn/Callable hint.

        Returns a concrete Fn/Callable type if a matching function is found,
        None to fall through to normal name analysis.
        """
        # Look up in namespace -- variables shadow functions
        if self.ctx.current_ns:
            binding = self.ctx.current_ns.lookup(expr.name)
            if binding:
                if binding.kind == BindingKind.VARIABLE:
                    return None  # local variable shadows any function
                if binding.kind == BindingKind.FUNCTION and binding.func_infos:
                    matched = self._match_function_to_hint(binding.func_infos, hint, expr)
                    if matched is not None:
                        expr.is_function_ref = True
                        expr.function_ref_info = matched
                        # Escape tracking: passing nested def to Callable marks it as escaping
                        if (isinstance(hint, CallableType)
                                and expr.name in self.ctx.nested_def_names):
                            self.ctx.nested_def_escapes.add(expr.name)
                        return self._concrete_fn_type(matched, expr, hint)

        # Check registry (covers imported functions not yet in namespace)
        func_infos = self.ctx.registry.get_function(expr.name)
        if func_infos:
            matched = self._match_function_to_hint(func_infos, hint, expr)
            if matched is not None:
                expr.is_function_ref = True
                expr.function_ref_info = matched
                return self._concrete_fn_type(matched, expr, hint)

        return None

    def _concrete_fn_type(
        self, fi: FunctionInfo, expr: TpyName, hint: FnType | CallableType,
    ) -> FnType | CallableType:
        """Build a concrete Fn/Callable type from a matched function's signature.

        When the hint has TypeParamRef (e.g. from a generic builtin like map[T,U]),
        returns the concrete type from the function's actual signature so that
        overload resolution can infer the outer type params.
        """
        if not contains_type_param(hint):
            return hint
        # Build concrete param/return types from the matched function info.
        # Strip Ref from param types and Own from return type -- FnType represents
        # the logical callable contract. Ref on return type IS preserved so type
        # inference can track reference semantics through combinators
        # (e.g. map(identity, pts) infers U=Ref[Point] -> val_or_ref<Point>).
        from ..typesys import unwrap_own
        param_types = tuple(unwrap_ref_type(ptype) for _, ptype in fi.params)
        return_type = unwrap_own(fi.return_type)
        if fi.is_generic() and expr.function_ref_type_args:
            subst = dict(zip(fi.type_params, expr.function_ref_type_args))
            param_types = tuple(
                self.type_ops.substitute_type_params(p, subst) for p in param_types
            )
            return_type = self.type_ops.substitute_type_params(return_type, subst)
        if isinstance(hint, CallableType):
            return CallableType(param_types, return_type)
        return FnType(param_types, return_type)

    def _match_function_to_hint(
        self, func_infos: list[FunctionInfo], hint: FnType | CallableType, expr: TpyName,
    ) -> FunctionInfo | None:
        """Find a function overload matching the Fn/Callable hint signature.

        Returns the matched FunctionInfo or raises an error if ambiguous.
        For generic functions, infers type parameters from the hint and stores
        the inferred type args on the expr node.
        """
        hint_params = hint.param_types
        hint_return = hint.return_type
        # Each candidate is (FunctionInfo, inferred_type_args_or_None)
        candidates: list[tuple[FunctionInfo, tuple[TpyType, ...] | None]] = []
        # Track first generic rejection for diagnostics
        generic_rejection: str | None = None
        for fi in func_infos:
            if len(fi.params) != len(hint_params):
                continue
            if fi.is_generic():
                type_args, rejection = self._infer_generic_ref_type_args(fi, hint)
                if type_args is not None:
                    candidates.append((fi, type_args))
                elif rejection is not None and generic_rejection is None:
                    generic_rejection = rejection
                continue
            match = True
            for (_, ptype), htype in zip(fi.params, hint_params):
                if isinstance(htype, TypeParamRef):
                    continue  # unresolved type param in hint -- wildcard match
                if ptype != htype:
                    try:
                        self.compat.check_type_compatible(htype, ptype, "param")
                    except SemanticError:
                        match = False
                        break
            if not match:
                continue
            if fi.return_type != hint_return:
                if isinstance(hint_return, (VoidType, TypeParamRef)):
                    # VoidType: Python semantics (callers discard the return value)
                    # TypeParamRef: unresolved type param in hint -- wildcard match
                    pass
                else:
                    try:
                        self.compat.check_type_compatible(fi.return_type, hint_return, "return")
                    except SemanticError:
                        continue
            candidates.append((fi, None))
        if len(candidates) == 1:
            fi, type_args = candidates[0]
            if type_args is not None:
                expr.function_ref_type_args = type_args
            return fi
        if len(candidates) > 1:
            raise self.ctx.error(
                f"Ambiguous function reference: multiple overloads of '{expr.name}' "
                f"match {hint}", expr)
        # No match -- emit generic rejection diagnostic if we have one
        if generic_rejection is not None:
            raise self.ctx.error(generic_rejection, expr)
        # Return None to fall through (might be a variable, not a function)
        return None

    def _infer_generic_ref_type_args(
        self, fi: FunctionInfo, hint: FnType | CallableType,
    ) -> tuple[tuple[TpyType, ...] | None, str | None]:
        """Try to infer type parameters for a generic function from an Fn/Callable hint.

        Returns (inferred_type_args, None) on success,
        (None, rejection_message) on bound or inference failure,
        (None, None) on type mismatch (not a candidate at all).
        """
        inferred: dict[str, TpyType] = {}
        # Match each function param type against the hint param type
        for (_, ptype), htype in zip(fi.params, hint.param_types):
            if not self.type_ops.match_type_with_inference(ptype, htype, inferred):
                return None, None
        # Match return type (unless hint is void -- any return is acceptable)
        if not isinstance(hint.return_type, VoidType):
            if not self.type_ops.match_type_with_inference(fi.return_type, hint.return_type, inferred):
                return None, None
        # Check all type params were inferred
        unresolved = [tp for tp in fi.type_params if tp not in inferred]
        if unresolved:
            return None, (
                f"Cannot use '{fi.name}' as function reference: "
                f"cannot infer type parameter(s) {', '.join(unresolved)} "
                f"from {hint}"
            )
        # Validate type parameter bounds
        for param_name, type_arg in inferred.items():
            if param_name in fi.type_param_bounds:
                bound = fi.type_param_bounds[param_name]
                if not self.protocols.type_conforms_to_protocol(type_arg, bound):
                    return None, (
                        f"Cannot use '{fi.name}' as function reference: "
                        f"inferred type argument {type_arg} for {param_name} "
                        f"does not satisfy bound '{bound.name}'"
                    )
        # Check for C++ param type mismatch (e.g. str: string_view vs const string&).
        # Generic functions use param_val_or_ref_t<T> which resolves based on the
        # storage type, but some types have a different param convention (str uses
        # string_view). This causes C++ compilation errors when the function is
        # passed through Fn/Callable.
        for param_name, type_arg in inferred.items():
            cpp_storage = type_arg.to_cpp()
            cpp_param = type_arg.to_cpp_param_type()
            # param_val_or_ref_t<T> resolves to const T& (value) or T& (object).
            # Accept T, T&, or const T& -- all compatible with the template.
            # Reject types with a different convention (e.g. str: string_view).
            if cpp_param not in (cpp_storage, f"{cpp_storage}&", f"const {cpp_storage}&"):
                return None, (
                    f"Cannot use '{fi.name}' as function reference with "
                    f"{param_name}={type_arg}: generic functions use a different "
                    f"C++ parameter convention than {type_arg} "
                    f"(use a lambda instead)"
                )
        return tuple(inferred[tp] for tp in fi.type_params), None
