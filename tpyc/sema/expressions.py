"""
TurboPython Expression Analysis

Core expression analysis including literals, names, operators, field access, and subscripts.
"""

from __future__ import annotations
from contextlib import ExitStack
from collections.abc import Callable as CallableFn
from dataclasses import replace as dc_replace
from typing import Literal, TYPE_CHECKING

from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, RecordInfo,
    NominalType, PtrType, OwnType, make_array, make_dict, make_set, make_span, make_list, span_as_const, span_as_mutable, PendingListType, ListRepeatType, GenExprType, TupleType,
    TypeParamRef, TypeParamKind, ListLiteralInfo, NoneType, AnyType, OptionalType, UnionType, VoidType,
    ReadonlyType, unwrap_readonly, unwrap_qualifiers, is_any_str_type, PendingStrType, PendingViewType,
    ValueForm,
    is_any_bytes_type, PendingBytesType,
    make_union,
    ResolvedBinop, FunctionInfo, ParamInfo, UnknownElementType, UNKNOWN_ELEMENT,
    PendingDictType, PendingSetType, DictLiteralInfo,
    resolve_int_literals, CallableType, make_fn_type, is_fn_type,
    INT32, FLOAT, STR, FSTR, STRVIEW, CHAR, BOOL, BIGINT, NONE, BASIC_SLICE, SLICE, BYTES, BYTESVIEW, UINT8,
    is_protocol_type, container_to_str_template, contains_type_param,
    PendingGenericInstanceType, unwrap_ref_type, unwrap_send_sync, make_ref, RefType,
    is_integer_type, is_any_int_type, is_union_or_optional_type,
    is_callable_type, is_float_type, is_any_float_type, is_numeric_type,
    unwrap_own, is_readonly_span,
    yield_uses_borrow_slot,
    RecursiveAliasInstanceType, recursive_union_alternatives)
from ..parse import (
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBytesLiteral,
    TpyFStringValue, TpyFString, FSTRING_CONV_REPR, FSTRING_CONV_STR,
    TpyBoolLiteral,
    TpyNoneLiteral, TpyName, TpyBinOp, TpyChainedCompare, TpyUnaryOp, TpyTypeParamConstruct,
    TpyCall, TpyMethodCall, TpyFieldAccess, TpyFunction,
    is_stable_address_lvalue,
    TpyArrayLiteral, TpyTupleLiteral, TpyDictLiteral, TpySetLiteral, TpyListRepeat,
    TpyListComprehension, TpyDictComprehension, TpySetComprehension, TpyGeneratorExpression, TpyComprehensionGenerator,
    TpySlice, TpySubscript, TpyCoerce,
    TpyIfExpr, TpyNamedExpr, TpyAwait,
    TpyLambda, TpyStarUnpack,
    TpyStmt, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyForEach, TpyWith,
    TpyNestedDef,
    collect_name_refs,
)
from .. import qnames
from ..type_def_registry import (
    is_set, is_dict, is_array, is_span, is_varargs, is_list,
    is_fixed_int_type, is_big_int_type, is_bool_type, is_char_type, is_fstr_type,
    is_basic_slice_type, is_slice_type,
    int_traits_of,
    is_enum_type, is_int_enum_type, enum_info_of,
    find_factory_by_simple_name, protocol_info_of,
)
from ..namespace import BindingKind, NameBinding
from .frame_traits import build_closure_frame
from ..coercions import CoercionContext
from ..prescan import _expr_to_narrowing_key
from ..diagnostics import SemanticError, OPTIONAL_NONE_ACCESS_WARNING
from .. import qnames
from .context import is_body_like_scope, register_binding_borrow, ephemeral_borrow_root
from .narrowing import NarrowingTracker, deref_view_narrowed
from .numeric_lattice import widen_numeric_types
from .list_literals import IterableHelper
from .local_deduction import collect_pending_source_types
from .operators import DUNDER_CPP_TEMPLATES, _substitute_type_params
from .bound_check import raise_if_class_param_bound_violated
from .overloads import resolve_overload

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


def _union_like_members(ut: TpyType) -> 'tuple[TpyType, ...]':
    """The variant alternatives of a recursive-union wrapper -- the non-generic
    UnionType's members or a generic instance's alternatives."""
    if isinstance(ut, UnionType):
        return ut.members
    return recursive_union_alternatives(ut) or ()


def _find_list_member(ut: TpyType) -> NominalType | None:
    """Find the list[...] member of a recursive-union wrapper, if any."""
    for m in _union_like_members(ut):
        if is_list(m):
            return m
    return None


def _find_dict_member(ut: TpyType) -> NominalType | None:
    """Find the dict[...] member of a recursive-union wrapper, if any."""
    for m in _union_like_members(ut):
        if is_dict(m):
            return m
    return None


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
        calls: CallAnalyzer,
        methods: MethodAnalyzer,
    ):
        self.ctx = ctx
        self.type_ops = type_ops
        self.operators = operators
        self.protocols = protocols
        self.compat = compat
        self.narrowing = narrowing
        self.calls = calls
        self.methods = methods
        self.scopes: ScopeTracker | None = None

    def set_scopes(self, scopes: ScopeTracker) -> None:
        """Set scope tracker (available once StatementAnalyzer is created)."""
        self.scopes = scopes

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

    def analyze_call_arg(self, arg: TpyExpr, hint: 'TpyType | None' = None) -> TpyType:
        """Analyze one call argument, transparently handling `*xs` unpack.

        Every call-argument pre-analyzer (generic inference, overload
        probing, vararg packing -- in both `calls.py` and `methods.py`)
        must route args through here rather than `analyze_expr` directly:
        a `TpyStarUnpack` carries no expression-type semantics of its own,
        so feeding it to the structural analyzer hits the catch-all
        "Unknown expression type" error. For a `*container` arg this
        returns the container's element type (the value an individual
        positional arg would have contributed); for everything else it
        is `analyze_expr[_with_hint]`.
        """
        if isinstance(arg, TpyStarUnpack):
            return self._unpack_star_element_type(arg, hint)
        if hint is not None:
            return self.analyze_expr_with_hint(arg, hint)
        return self.analyze_expr(arg)

    def _unpack_star_element_type(
        self, node: TpyStarUnpack, elem_hint: 'TpyType | None'
    ) -> TpyType:
        """Element type of the container unpacked by `*node.expr`.

        `elem_hint`, when present, is the vararg parameter's element type;
        we lift it to `list[elem_hint]` so the inner container literal can
        resolve its own element type from context (mirrors the seeded-hint
        path other args get).

        Only directly-iterable lvalue containers (list / span / array) are
        accepted -- the same set the vararg-pack codegen can lower via
        `as_mut_span`. A reference-type container *parameter* (e.g.
        `xs: list[T]`) carries a `Ref[...]` wrapper from by-reference passing;
        that is just the borrow form codegen already emits, so unwrap it.
        Owning-rvalue (`Own[list[...]]`) and other wrapped shapes are
        deliberately NOT unwrapped: sema must not accept a shape codegen can't
        emit (the owning-rvalue unpack gap is tracked in TODO.md).
        """
        inner_hint = make_list(elem_hint) if elem_hint is not None else None
        if inner_hint is not None:
            inner_type = self.analyze_expr_with_hint(node.expr, inner_hint)
        else:
            inner_type = self.analyze_expr(node.expr)
        inner_type = unwrap_ref_type(inner_type)
        elem: 'TpyType | None' = None
        if (is_array(inner_type) or is_span(inner_type) or is_varargs(inner_type)
                or is_list(inner_type)):
            elem = inner_type.get_element_type()
        elif isinstance(inner_type, PendingListType):
            elem = inner_type.element_type
        if elem is None:
            raise self.ctx.error(
                f"Cannot unpack type '{self._user_type_name(inner_type)}' "
                f"into *args", node)
        return elem

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
        elif isinstance(expr, TpyAwait):
            typ = self._analyze_await(expr)
        elif isinstance(expr, TpyLambda):
            typ = self._analyze_lambda(expr)
        elif isinstance(expr, TpyCoerce):
            # Coercions are attached post-analysis; treat as the expected type.
            typ = expr.expected_type
        elif isinstance(expr, TpyStarUnpack):
            # `*xs` is only meaningful at a variadic-accepting call position,
            # where the handler routes it through `analyze_call_arg` (element
            # extraction) instead of here. Reaching the structural analyzer
            # means the surrounding call target does not accept *args -- reject
            # cleanly rather than falling through to the "Unknown expression
            # type" catch-all (or silently mis-typing the unpack as one arg).
            raise self.ctx.error(
                "Cannot use *unpacking here: the call target does not accept "
                "*args (only a variadic parameter can receive `*iterable`)",
                expr)
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

        # Lambda with Fn/Callable type hint: infer param types from the hint.
        # Send/Sync markers constrain the conversion (checked by
        # _check_compat), not the callable's shape -- see through them here.
        lambda_hint = unwrap_send_sync(type_hint)
        if isinstance(expr, TpyLambda) and is_callable_type(lambda_hint):
            typ = self._analyze_lambda_with_fn_hint(expr, lambda_hint)
            self.ctx.set_expr_type(expr, typ)
            return typ

        # Named function reference with Fn/Callable hint: resolve as function value.
        # Unwrap OwnType so that e.g. list.append(Own[Callable[...]]) works.
        fn_hint = lambda_hint.wrapped if isinstance(lambda_hint, OwnType) else lambda_hint
        if isinstance(expr, TpyName) and is_callable_type(fn_hint):
            result = self._try_resolve_function_ref(expr, fn_hint)
            if result is not None:
                self.ctx.set_expr_type(expr, result)
                return result

        # Ternary expression: propagate hint to both branches
        if isinstance(expr, TpyIfExpr):
            typ = self._analyze_if_expr(expr, type_hint=type_hint)
            self.ctx.set_expr_type(expr, typ)
            return typ

        # F-string with FStr hint: keep decomposed for macro consumption.
        # Skip format-validity checks -- the call macro handles per-type dispatch.
        if isinstance(expr, TpyFString) and is_fstr_type(type_hint):
            self._analyze_fstring(expr, for_fstr=True)
            self.ctx.set_expr_type(expr, FSTR)
            return FSTR

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
        _generic_td = (find_factory_by_simple_name(expr.func_name)
                       if isinstance(expr, TpyCall) and isinstance(expr.func, TpyName) else None)
        is_generic_constructor = (isinstance(expr, TpyCall) and
                                  not expr.args and
                                  expr.call_type is None and
                                  _generic_td is not None and
                                  bool(_generic_td.param_kinds))

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
                td = find_factory_by_simple_name(expr.func_name)  # type: ignore
                hint_matches = (td is not None and
                                inner_hint.qualified_name() == td.qname)
            else:
                # Empty literal [] can match list[T] hint
                hint_matches = is_list(inner_hint)

            if hint_matches:
                if is_list(inner_hint):
                    # list[T]: Use PendingListType for potential Array optimization
                    elem_type = inner_hint.get_element_type()
                    # Set call_type so codegen generates explicit type (e.g., std::vector<int>())
                    if is_generic_constructor:
                        expr.call_type = inner_hint  # type: ignore
                    if self.ctx.func.current_function is None:
                        typ = make_list(elem_type)
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
                        self.ctx.func.pending_resolutions.append(literal_id)
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
            if is_array(inner_hint) or is_list(inner_hint):
                result = self._analyze_array_literal(expr, inner_hint.get_element_type())
                if isinstance(result, PendingListType):
                    info = self.ctx.list_literals.get(result.literal_id)
                    if info:
                        info.has_explicit_annotation = True
                        info.explicit_type = inner_hint
                        if info.coerced_element_type is None:
                            info.coerced_element_type = inner_hint.get_element_type()
                self.ctx.set_expr_type(expr, result)
                return result
            # Recursive union type with a list member: propagate the union
            # as element hint so nested list literals infer as list[Tree]
            # (e.g. x: Tree = [1, [3, 4]] where type Tree = int | list[Tree])
            if inner_hint.needs_wrapper():
                list_member = _find_list_member(inner_hint)
                if list_member is not None:
                    result = self._analyze_array_literal(expr, inner_hint)
                    if isinstance(result, PendingListType):
                        info = self.ctx.list_literals.get(result.literal_id)
                        if info:
                            info.has_explicit_annotation = True
                            info.explicit_type = list_member
                            if info.coerced_element_type is None:
                                info.coerced_element_type = inner_hint
                    self.ctx.set_expr_type(expr, result)
                    return result

        # List comprehension with list type hint: propagate element type
        if isinstance(expr, TpyListComprehension):
            inner_hint = unwrap_readonly(type_hint)
            if isinstance(inner_hint, OwnType):
                inner_hint = inner_hint.wrapped
            if is_list(inner_hint):
                typ = self._analyze_list_comprehension(expr, expected_elem=inner_hint.get_element_type())
                if isinstance(typ, PendingListType):
                    info = self.ctx.list_literals.get(typ.literal_id)
                    if info:
                        info.has_explicit_annotation = True
                        info.explicit_type = inner_hint
                self.ctx.set_expr_type(expr, typ)
                return typ
            if is_array(inner_hint):
                typ = self._analyze_list_comprehension(expr, expected_elem=inner_hint.get_element_type())
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
            if is_dict(inner_hint):
                typ = self._analyze_dict_comprehension(
                    expr, expected_key=inner_hint.type_args[0],
                    expected_value=inner_hint.type_args[1])
                self.ctx.set_expr_type(expr, typ)
                return typ

        # Dict literal with dict type hint
        if isinstance(expr, TpyDictLiteral):
            inner_hint = unwrap_readonly(type_hint)
            if isinstance(inner_hint, OwnType):
                inner_hint = inner_hint.wrapped
            if is_dict(inner_hint):
                result = self._analyze_dict_literal(expr, inner_hint.type_args[0], inner_hint.type_args[1])
                self.ctx.set_expr_type(expr, result)
                return result
            if inner_hint.needs_wrapper():
                dict_member = _find_dict_member(inner_hint)
                if dict_member is not None:
                    result = self._analyze_dict_literal(expr, dict_member.type_args[0], inner_hint)
                    self.ctx.set_expr_type(expr, result)
                    return result

        # Set comprehension with set type hint: propagate element type
        if isinstance(expr, TpySetComprehension):
            inner_hint = unwrap_readonly(type_hint)
            if isinstance(inner_hint, OwnType):
                inner_hint = inner_hint.wrapped
            if is_set(inner_hint):
                typ = self._analyze_set_comprehension(
                    expr, expected_elem=inner_hint.type_args[0])
                self.ctx.set_expr_type(expr, typ)
                return typ

        # Non-empty set literal with set type hint
        if isinstance(expr, TpySetLiteral) and expr.elements:
            inner_hint = unwrap_readonly(type_hint)
            if isinstance(inner_hint, OwnType):
                inner_hint = inner_hint.wrapped
            if is_set(inner_hint):
                result = self._analyze_set_literal(expr, inner_hint.type_args[0])
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
        if expr.name in self.ctx.func.consumed_vars:
            raise self.ctx.error(
                f"Cannot use '{expr.name}' after it was consumed by a consuming method call",
                expr,
            )

        # No ephemeral-borrow closure-capture check is needed: an escaping closure
        # is Callable-typed and captures by value (copies the borrow's value -- safe),
        # while a by-reference Fn-typed closure is inline / non-escaping (used within
        # the iteration step). So no closure can retain an ephemeral frame-slot borrow
        # past its step.

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
        if self.ctx.func.current_ns:
            binding = self.ctx.func.current_ns.lookup(expr.name)
            if binding:
                if binding.kind == BindingKind.VARIABLE:
                    self._check_definitely_assigned(expr)
                    result = self.narrowing.narrow_name_type(expr.name, binding.type)
                    return self._apply_own_wrapper(expr, result)
                if binding.kind == BindingKind.BUILTIN:
                    return binding.type
                # For other bindings (FUNCTION, RECORD, MODULE, IMPORTED_NAME),
                # the name exists but isn't usable as a variable
                raise self.ctx.error(f"'{expr.name}' is not a variable", expr)

        # Migration bridge: scope may contain names not yet in namespace
        # (e.g., during incremental namespace adoption). Remove once all
        # name registration flows go through Namespace.
        typ = self.ctx.func.current_scope.lookup(expr.name)
        if typ is None:
            # Check built-in names (like __name__)
            if expr.name in self.ctx.builtin_names:
                return self.ctx.builtin_names[expr.name]
            # Lazy promotion of loop-scoped variables referenced after the loop
            if self._promote_pending_loop_var(expr.name):
                typ = self.ctx.func.current_scope.lookup(expr.name)
            else:
                raise self.ctx.error(f"Undefined variable: '{expr.name}'", expr)
        self._check_definitely_assigned(expr)
        result = self.narrowing.narrow_name_type(expr.name, typ)
        return self._apply_own_wrapper(expr, result)

    def _apply_own_wrapper(self, expr: TpyName, result: TpyType) -> TpyType:
        """Derive OwnType wrapping at use time for local variable names.

        Params keep their FunctionInfo type (Ref/Own is a C++ contract).
        Owned locals (rvalue-init, not ref-returning) get OwnType at
        non-last-use, bare T at last-use (auto-move).
        """
        name = expr.name
        # Params: FunctionInfo type already carries Ref/Own.
        # Reassigned params fall through to local derivation.
        if name in self.ctx.func.current_param_names:
            if name not in self.ctx.func.current_reassigned_vars:
                # Strip Own at last-use for auto-move (same as before)
                if isinstance(result, OwnType) and id(expr) in self.ctx.all_last_uses:
                    return result.wrapped
                return result
        # Locals: derive OwnType from owned_locals set.
        # Scope stores bare T; wrap when owned and not at last-use.
        if (not result.is_value_type()
                and not isinstance(result, (OwnType, RefType, NoneType,
                                            PendingListType, PendingDictType, PendingSetType,
                                            PendingViewType, PendingGenericInstanceType))
                and name in self.ctx.func.owned_locals
                and name not in self.ctx.func.hoisted_vars
                and name not in self.ctx.func.loop_vars):
            if id(expr) not in self.ctx.all_last_uses:
                return OwnType(result)
        return result

    def _check_definitely_assigned(self, expr: TpyName) -> None:
        """Check that a local variable is definitely assigned before use."""
        if (not self.ctx.func.init_terminated
                and expr.name in self.ctx.func.var_scope_depth
                and self.ctx.func.var_scope_depth[expr.name] >= 1
                and expr.name not in self.ctx.func.definitely_assigned):
            raise self.ctx.error(
                f"variable '{expr.name}' may not be assigned at this point", expr)

    def _promote_pending_loop_var(self, name: str) -> bool:
        """Promote a pending for-loop-scoped variable if present.

        Returns True if the variable was promoted (added to scope and
        definitely_assigned, registered for codegen pre-declaration).
        """
        pending = self.ctx.func.pending_loop_vars.pop(name, None)
        if pending is None:
            return False
        var_type, loop_stmt, orig_stmt = pending
        self.ctx.func.current_scope.define(name, var_type)
        self.ctx.func.definitely_assigned.add(name)
        # Register for codegen pre-declaration
        decls = self.ctx.if_branch_decls.setdefault(id(loop_stmt), {})
        decls[name] = var_type
        # Mark the original for-loop's var for hoisted codegen (hidden counter)
        if isinstance(orig_stmt, TpyForEach) and name == orig_stmt.var:
            orig_stmt.hoist_loop_var = True
        return True

    def _normalize_pending_container(self, t: TpyType) -> TpyType:
        """Normalize a pending container type to a concrete type with resolved IntLiteralType elements.

        Used in or/and/ternary type comparison: two PendingListType literals with the same
        element type but different IDs (or different IntLiteralType values) are compatible.
        """
        if isinstance(t, PendingListType):
            return resolve_int_literals(make_list(t.element_type), self.ctx.default_int_for_literal)
        if isinstance(t, PendingDictType):
            k = self.ctx.default_int_for_literal(t.key_type) if isinstance(t.key_type, IntLiteralType) else t.key_type
            k = FLOAT if isinstance(k, FloatLiteralType) else k
            v = self.ctx.default_int_for_literal(t.value_type) if isinstance(t.value_type, IntLiteralType) else t.value_type
            v = FLOAT if isinstance(v, FloatLiteralType) else v
            return make_dict(k, v)
        if isinstance(t, PendingSetType):
            elem = self.ctx.default_int_for_literal(t.element_type) if isinstance(t.element_type, IntLiteralType) else t.element_type
            elem = FLOAT if isinstance(elem, FloatLiteralType) else elem
            return make_set(elem)
        return t

    def _logical_op_result_type(self, left: TpyType, right: TpyType) -> TpyType:
        """Determine result type for and/or operators.

        Python semantics: `x and y` returns an operand, not bool.
        Same non-bool type -> return that type (enables value-context usage).
        Different types or bool operands -> return bool.
        """
        # Bool operands: C++ &&/|| already correct
        if is_bool_type(left) or is_bool_type(right):
            return BOOL
        # Resolve int literals to match concrete int type on the other side
        if isinstance(left, IntLiteralType):
            if is_integer_type(right):
                left = right
            elif isinstance(right, IntLiteralType):
                return self.ctx.default_int_type
            else:
                return BOOL
        elif isinstance(right, IntLiteralType):
            if is_integer_type(left):
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
            saved_types = dict(self.ctx.func.narrowed_types)
            # Save definitely_assigned: RHS may not execute due to short-circuit
            saved_assigned = frozenset(self.ctx.func.definitely_assigned)
            if expr.op == "&&":
                self.ctx.func.narrowed_types.update(type_true)
            else:
                self.ctx.func.narrowed_types.update(type_false)
            try:
                right_type = self.analyze_expr(expr.right)
            finally:
                self.ctx.func.narrowed_types = saved_types
                # Track walrus vars introduced in RHS (short-circuit conditional)
                rhs_walrus = self.ctx.func.definitely_assigned - saved_assigned
                if rhs_walrus:
                    if expr.op == "&&":
                        self.ctx.sc_and_walrus |= rhs_walrus
                    else:
                        self.ctx.sc_or_walrus |= rhs_walrus
                # Rollback: RHS walrus vars are not definitely assigned
                self.ctx.func.definitely_assigned = set(saved_assigned)
        else:
            right_type = self.analyze_expr(expr.right)

        # Preserve declared Optional/Union type for identity checks when flow
        # narrowing resolved an expression to its inner type.
        if expr.op in ("is", "is not"):
            declared_left = self.narrowing.declared_type_for_expr(expr.left)
            if is_union_or_optional_type(declared_left):
                left_type = declared_left
            declared_right = self.narrowing.declared_type_for_expr(expr.right)
            if is_union_or_optional_type(declared_right):
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

        # Unwrap OwnType/RefType -- ownership/references don't affect operator resolution
        left_effective = unwrap_ref_type(left_type)
        left_effective = left_effective.wrapped if isinstance(left_effective, OwnType) else left_effective
        right_effective = unwrap_ref_type(right_type)
        right_effective = right_effective.wrapped if isinstance(right_effective, OwnType) else right_effective
        # Value optionals in operator expressions use runtime null checks unless
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
            return is_any_int_type(t) or is_any_float_type(t)

        # Identity operators (is / is not) -- only valid with None or enums
        if expr.op in ("is", "is not"):
            # Unwrap RefType, ReadonlyType, and OwnType for nullable checks.
            left_check = unwrap_ref_type(unwrap_readonly(left_type))
            if isinstance(left_check, OwnType):
                left_check = left_check.wrapped
            right_check = unwrap_ref_type(unwrap_readonly(right_type))
            if isinstance(right_check, OwnType):
                right_check = right_check.wrapped
            # Enum identity: lower to ==/!=
            if is_enum_type(left_check) and is_enum_type(right_check):
                if left_check.name == right_check.name:
                    expr.op = "==" if expr.op == "is" else "!="
                    return BOOL
                raise self.ctx.error(
                    f"Cannot compare enum types '{left_check.name}' and '{right_check.name}'",
                    expr,
                )
            # Bool literal identity: lower to ==/!=
            if is_bool_type(left_check) and isinstance(expr.right, TpyBoolLiteral):
                expr.op = "==" if expr.op == "is" else "!="
                return BOOL
            if is_bool_type(right_check) and isinstance(expr.left, TpyBoolLiteral):
                expr.op = "==" if expr.op == "is" else "!="
                return BOOL
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
            # Any vs None: typeid-based check (D15). Other RHS forms of
            # `is` on Any are still rejected -- see the Future Extensions
            # row in docs/ANY_TYPE_DESIGN.md.
            if isinstance(right_check, NoneType) and isinstance(left_check, AnyType):
                return BOOL
            if isinstance(left_check, NoneType) and isinstance(right_check, AnyType):
                return BOOL
            raise self.ctx.error(
                f"'is' / 'is not' can only compare Optional/Ptr/union types with None, "
                f"got {left_type} and {right_type}",
                expr,
            )

        # Enum operators: base Enum supports == and != only;
        # IntEnum also supports ordering, comparison with integers, and arithmetic
        if is_enum_type(left_effective) or is_enum_type(right_effective):
            left_is_int_enum = is_int_enum_type(left_effective)
            right_is_int_enum = is_int_enum_type(right_effective)
            is_comparison = expr.op in ("==", "!=", "<", ">", "<=", ">=")

            if is_comparison:
                # IntEnum vs integer: coerce enum to underlying type
                if left_is_int_enum and is_any_int_type(right_effective):
                    expr.int_enum_coercion = left_effective
                    return BOOL
                if right_is_int_enum and is_any_int_type(left_effective):
                    expr.int_enum_coercion = right_effective
                    return BOOL
                if is_enum_type(left_effective) and is_enum_type(right_effective):
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
                enum_name = left_effective.name if is_enum_type(left_effective) else right_effective.name
                raise self.ctx.error(
                    f"Cannot compare '{enum_name}' with '{right_effective if is_enum_type(left_effective) else left_effective}'",
                    expr,
                )

            # Non-comparison ops: IntEnum arithmetic is handled below;
            # base Enum in arithmetic is an error
            if not left_is_int_enum and not right_is_int_enum:
                enum_name = left_effective.name if is_enum_type(left_effective) else right_effective.name
                raise self.ctx.error(
                    f"Operator '{expr.op}' not supported for enum type '{enum_name}'",
                    expr,
                )

        # Tuple comparison: ==, !=, <, <=, >, >= with element-wise validation
        if isinstance(left_effective, TupleType) or isinstance(right_effective, TupleType):
            # Both shapes (membership in a tuple literal `x in (a, b, c)` and
            # tuple-as-key `key in dict`) are handled by the in/not-in section
            # below; fall through here.
            if expr.op in ("in", "not in"):
                pass
            elif expr.op in ("==", "!=", "<", "<=", ">", ">="):
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
                    # For ordering ops, validate that element types support the operator
                    if expr.op in ("<", "<=", ">", ">="):
                        # An Optional element has no ordering: CPython raises
                        # TypeError when a None meets `<`, and a null
                        # pointer-repr slot would be dereferenced by the C++
                        # lexicographic compare (tuple_lt's non-null contract).
                        if (isinstance(unwrap_readonly(lt), OptionalType)
                                or isinstance(unwrap_readonly(rt), OptionalType)):
                            opt_t = (lt if isinstance(unwrap_readonly(lt), OptionalType)
                                     else rt)
                            raise self.ctx.error(
                                f"Cannot order tuples on element {i} "
                                f"('{opt_t}'): ordering is undefined for an "
                                f"optional element (None does not support "
                                f"'{expr.op}')",
                                expr,
                            )
                        self._validate_comparison(expr, lt, rt)
                return BOOL
            else:
                raise self.ctx.error(
                    f"Operator '{expr.op}' is not supported for tuple types",
                    expr,
                )

        # Comparison operators return Bool
        if expr.op in ("==", "!=", "<", ">", "<=", ">="):
            # Mixed-sign fixed-int comparison is well-defined under C++'s usual
            # arithmetic conversions (the signed operand is reinterpreted as
            # unsigned), but the result rarely matches user intent on negative
            # values. Codegen routes these through std::cmp_* so the answer is
            # mathematically correct; warn here so the user can choose to
            # cast explicitly if they care about readability. Skipped when
            # either side is still literal-seeded -- retro-widening may yet
            # resolve the operand to a compatible type.
            if (not self._is_literal_seed_operand(expr.left)
                    and not self._is_literal_seed_operand(expr.right)
                    and is_fixed_int_type(left_effective)
                    and is_fixed_int_type(right_effective)):
                lt = int_traits_of(left_effective)
                rt = int_traits_of(right_effective)
                if lt is not None and rt is not None and lt.signed != rt.signed:
                    self.ctx.warning(
                        f"comparison between signed and unsigned integer types "
                        f"('{left_effective}' and '{right_effective}'); "
                        f"cast one operand to make the type intent explicit",
                        expr,
                    )
            # Validate that user record types support the comparison
            self._validate_comparison(expr, left_effective, right_effective)
            # Resolve comparison method (__eq__, __lt__, etc.) for codegen.
            if result := self.operators.resolve_binop(left_effective, expr.op, right_effective, loc_node=expr):
                expr.resolved_binop = result
            elif expr.op == "!=":
                # No __ne__: fall back to negated __eq__ when the method
                # can't use raw C++ != (e.g. native freestanding function).
                if result := self.operators.resolve_binop(left_effective, "==", right_effective, loc_node=expr):
                    if result.method.native_function:
                        expr.resolved_binop = result
            return BOOL

        # Membership operators (in, not in) return Bool
        if expr.op in ("in", "not in"):
            right_type = unwrap_ref_type(right_type)
            if isinstance(right_type, OwnType):
                right_type = right_type.wrapped
            # TypedDict: "key" in td -> compile-time field presence check
            right_record = self.ctx.registry.get_record_for_type(right_type)
            if right_record and right_record.is_typed_dict:
                if not isinstance(expr.left, TpyStrLiteral):
                    raise self.ctx.error(
                        f"TypedDict '{right_type.name}' membership test requires a string literal key",
                        expr.left)
                key = expr.left.value
                for fld in right_record.fields:
                    if fld.name == key:
                        expr.typed_dict_in_field = key
                        expr.typed_dict_in_always_true = not right_record.is_total_false
                        return BOOL
                raise self.ctx.error(
                    f"TypedDict '{right_type.name}' has no key '{key}'", expr.left)
            if right_record:
                contains_overloads = right_record.get_method_overloads("__contains__")
                if contains_overloads:
                    # Drop int-kind type args (e.g. N in Array[T, N]) --
                    # _substitute_type_params only acts on TypeParamRef -> TpyType.
                    type_subst = {
                        k: v for k, v in self.type_ops.build_type_substitution(right_type).items()
                        if isinstance(v, TpyType)
                    }
                    # Substitute type params for generic containers
                    subst_overloads = contains_overloads
                    if type_subst:
                        subst_overloads = [
                            dc_replace(m, params=[
                                ParamInfo(p.name, _substitute_type_params(p.type, type_subst))
                                for p in m.params
                            ]) for m in contains_overloads
                        ]
                    # Resolve IntLiteralType: use param type if the literal fits,
                    # otherwise fall back to default int type
                    check_left = left_type
                    if isinstance(check_left, IntLiteralType):
                        for m in subst_overloads:
                            pt = m.params[0].type
                            pt_tr = int_traits_of(pt)
                            if (pt_tr is not None
                                    and pt_tr.min_value <= check_left.value <= pt_tr.max_value):
                                check_left = pt
                                break
                        else:
                            check_left = self.ctx.default_int_for_literal(check_left)
                    matched = resolve_overload(subst_overloads, [check_left])
                    if matched is not None:
                        # Map back to the original (un-substituted) method for codegen
                        idx = subst_overloads.index(matched)
                        original_method = contains_overloads[idx]
                        raise_if_class_param_bound_violated(
                            original_method, right_record.type_params, type_subst,
                            self.protocols.type_conforms_to_protocol,
                            self.ctx.error, expr,
                        )
                        expr.resolved_contains = original_method
                        return BOOL
                    # No __contains__ overload matched. Defer to
                    # check_type_compatible: it raises for genuine mismatches
                    # (preserving int-literal range diagnostics) and accepts
                    # compatible coercions, in which case control falls
                    # through to the outer iterable-membership path.
                    int_overload = None
                    if isinstance(left_type, IntLiteralType):
                        for o in subst_overloads:
                            if int_traits_of(o.params[0].type) is not None:
                                int_overload = o
                                break
                    if int_overload is not None or len(subst_overloads) == 1:
                        param_type = (int_overload or subst_overloads[0]).params[0].type
                        self.compat.check_type_compatible(
                            left_type, param_type,
                            f"membership test (expected {param_type})",
                            loc=expr.loc,
                        )
                    else:
                        expected_str = " or ".join(
                            str(o.params[0].type) for o in subst_overloads)
                        raise self.ctx.error(
                            f"Type mismatch in membership test: "
                            f"expected {expected_str}, got {left_type}",
                            expr,
                        )
            # Tuple literal membership: x in (1, 2, 3) -> x == 1 || x == 2 || x == 3
            if isinstance(right_type, TupleType) and isinstance(expr.right, TpyTupleLiteral):
                for et in right_type.element_types:
                    if not self.compat.is_type_compatible(left_type, et) \
                       and not self.compat.is_type_compatible(et, left_type):
                        raise self.ctx.error(
                            f"Tuple element type '{et}' is not compatible "
                            f"with membership test type '{left_type}'",
                            expr)
                return BOOL
            # Right side must be iterable (intrinsically or via NativeIterable protocol)
            helper = IterableHelper(self.ctx)
            if helper.is_type_iterable(right_type):
                # For string containers, LHS must be str or Char
                if is_any_str_type(right_type):
                    if not (is_any_str_type(left_type) or is_char_type(left_type)):
                        raise SemanticError(
                            f"Cannot check '{left_type}' membership in str (expected str or Char)",
                            expr.loc
                        )
                else:
                    # Non-string collections use std::find which requires ==
                    elem_type = right_type.get_element_type()
                    if elem_type is not None:
                        equatable = NominalType("Equatable", is_protocol=True)
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
            if is_list(result):
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
            if is_int_enum_type(left_effective):
                int_enum_side = left_effective
                other_side = right_effective
            elif is_int_enum_type(right_effective):
                int_enum_side = right_effective
                other_side = left_effective
            if int_enum_side is not None:
                if is_int_enum_type(other_side):
                    if other_side.name != int_enum_side.name:
                        raise self.ctx.error(
                            f"Cannot mix arithmetic between '{int_enum_side.name}' "
                            f"and '{other_side.name}'",
                            expr,
                        )
                if (is_int_enum_type(other_side) or isinstance(other_side, IntLiteralType)
                        or is_fixed_int_type(other_side) or is_big_int_type(other_side)):
                    # Coerce IntEnum operands to underlying type so standard
                    # FixedInt binop resolution (with checked arithmetic) handles it
                    expr.int_enum_coercion = int_enum_side
                    if is_int_enum_type(left_effective):
                        left_effective = enum_info_of(left_effective).underlying_type
                    if is_int_enum_type(right_effective):
                        right_effective = enum_info_of(right_effective).underlying_type
                    # Fall through to standard binop resolution below

        # IntLiteral + IntLiteral -> IntLiteral (stays unresolved until context determines type)
        if isinstance(left_effective, IntLiteralType) and isinstance(right_effective, IntLiteralType):
            # Keep Python-style true division semantics for all-literal integer
            # expressions regardless of default-int setting.
            if expr.op == "div":
                if result := self.operators.resolve_binop(BIGINT, expr.op, BIGINT, loc_node=expr):
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
            if result := self.operators.resolve_binop(resolved_int, expr.op, resolved_int, loc_node=expr):
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
        if result := self.operators.resolve_binop(left_effective, expr.op, right_effective, loc_node=expr):
            expr.resolved_binop = result
            # Check if divisor is provably non-zero for div/mod elision
            if expr.op in ("//", "%"):
                self._check_divisor_non_zero(expr)
            # List concat produces a list -- mark pending literals as mutated
            if is_list(result.method.return_type):
                self._mark_list_concat_operands_mutated(expr, left_effective, right_effective)
            return result.method.return_type

        # Record types (user-defined or module) with dunder methods
        if isinstance(left_effective, NominalType) and left_effective.is_record:
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
                            raise_if_class_param_bound_violated(
                                method, record.type_params, type_subst,
                                self.protocols.type_conforms_to_protocol,
                                self.ctx.error, expr,
                            )
                            ret_type = method.return_type
                            if type_subst:
                                ret_type = self.type_ops.substitute_types(ret_type, type_subst)
                            # Build ResolvedBinop so codegen uses the method's
                            # cpp_template instead of raw C++ operator syntax.
                            cpp = method.cpp_template
                            if not cpp and not method.native_function:
                                cpp = DUNDER_CPP_TEMPLATES.get(method_name)
                            resolved_method = FunctionInfo(
                                name=method_name,
                                params=list(method.params),
                                return_type=ret_type,
                                cpp_template=cpp,
                                native_name=method.native_name,
                                native_function=method.native_function,
                                is_method=True,
                                owning_type_qname=method.owning_type_qname,
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
        index_range = self.ctx.func.value_ranges.get(index.name)
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
        divisor_range = self.ctx.func.value_ranges.get(right.name)
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
                if var_name in self.ctx.func.variable_to_literal:
                    lit_id = self.ctx.func.variable_to_literal[var_name]
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
        effective_type = unwrap_ref_type(operand_type)
        if isinstance(effective_type, OwnType):
            effective_type = effective_type.wrapped

        # Value optionals in unary arithmetic/bitwise ops use runtime checks
        # unless flow already narrowed them to non-Optional.
        if expr.op in ("-", "~") and isinstance(operand_type, OptionalType) and operand_type.inner.is_value_type():
            effective_type = operand_type.inner
            self.ctx.warning(OPTIONAL_NONE_ACCESS_WARNING, expr)

        # Logical not: validate operand type (Bool, numeric, Optional, or types with __bool__/__len__)
        if expr.op == "!":
            if (is_bool_type(effective_type)
                    or is_any_int_type(effective_type)
                    or is_any_float_type(effective_type)
                    or isinstance(effective_type, OptionalType)
                    or isinstance(effective_type, AnyType)
                    or is_enum_type(effective_type)):
                return BOOL
            record = self.ctx.registry.get_record_for_type(effective_type)
            if record and (record.get_method_overloads("__bool__")
                           or record.get_method_overloads("__len__")):
                return BOOL
            raise self.ctx.error(f"Invalid operand type for 'not': {effective_type} (expected bool, numeric, or type with __bool__/__len__)", expr)

        # Float types support unary negation and plus
        if is_any_float_type(effective_type):
            if expr.op in ("-", "+"):
                if result := self.operators.resolve_unaryop(effective_type, expr.op, loc_node=expr):
                    expr.resolved_unaryop = result
                return effective_type

        # IntEnum: unary negation returns the underlying integer type
        if is_int_enum_type(effective_type) and expr.op == "-":
            return enum_info_of(effective_type).underlying_type

        # IntLiteralType special cases - preserve literal nature when possible
        if isinstance(effective_type, IntLiteralType):
            if expr.op == "-":
                # Still resolve for codegen (needs cpp template)
                if result := self.operators.resolve_unaryop(effective_type, expr.op, loc_node=expr):
                    expr.resolved_unaryop = result
                neg = -effective_type.value if effective_type.value is not None else None
                return IntLiteralType(neg)
            if expr.op == "+":
                if result := self.operators.resolve_unaryop(effective_type, expr.op, loc_node=expr):
                    expr.resolved_unaryop = result
                return effective_type
            if expr.op == "~":
                # Bitwise not on literal - treat as Int32
                # Still resolve for codegen
                if result := self.operators.resolve_unaryop(effective_type, expr.op, loc_node=expr):
                    expr.resolved_unaryop = result
                return INT32

        # Use registry for unary operators
        if result := self.operators.resolve_unaryop(effective_type, expr.op, loc_node=expr):
            expr.resolved_unaryop = result
            return result.method.return_type

        raise self.ctx.error(f"Invalid operand type for unary '{expr.op}': {operand_type}", expr)

    def _is_user_record_type(self, typ: TpyType) -> bool:
        """Check if a type is a user-defined record (not a builtin container)."""
        return isinstance(typ, NominalType) and typ.is_record and typ.is_user_record

    def _is_literal_seed_operand(self, operand: TpyExpr) -> bool:
        """True when ``operand`` is still pending literal-driven type resolution.

        Two cases: an analyzer-level ``IntLiteralType`` (the operand is itself a
        literal), or a ``TpyName`` whose local is in ``literal_default_vars``
        (literal-seeded local awaiting retro-widen). Used to suppress the
        mixed-sign-comparison warning before sema has finalised the type --
        the operand may yet resolve to a same-sign type.
        """
        if isinstance(self.ctx.get_expr_type(operand), IntLiteralType):
            return True
        return (isinstance(operand, TpyName)
                and operand.name in self.ctx.func.literal_default_vars)

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
        if is_basic_slice_type(typ):
            if expr.field in ("start", "stop"):
                return OptionalType(INT32)
            return None
        if is_slice_type(typ):
            if expr.field in ("start", "stop", "step"):
                return OptionalType(INT32)
            return None

        if isinstance(typ, NominalType) and typ.is_record:
            record = self.ctx.registry.get_record_for_type(typ)
            if not record:
                return None
            # Multi-base same-name ambiguity: when the child doesn't declare
            # the field itself and more than one direct-parent branch reaches
            # it, unqualified access could silently pick the first hit --
            # reject instead so the user disambiguates via `BaseN.field`.
            child_owns_field = any(f.name == expr.field for f in record.fields)
            if not child_owns_field and len(record.parents) > 1:
                branches = self.protocols.find_field_parent_branches(record, expr.field)
                if len(branches) > 1:
                    names = ", ".join(branches)
                    first, second = branches[0], branches[1]
                    raise self.ctx.error(
                        f"Ambiguous field '{expr.field}' inherited from {{{names}}} "
                        f"in '{record.name}'; use '{first}.{expr.field}' "
                        f"or '{second}.{expr.field}'",
                        expr,
                    )
            type_subst = self.type_ops.build_type_substitution(typ)
            field_info = self.protocols.lookup_record_field(record, expr.field)
            if field_info:
                field_type = field_info.type
                if type_subst:
                    field_type = self.type_ops.substitute_type_params(field_type, type_subst)
                return field_type
            # Check properties (getter access)
            prop = self.protocols.lookup_record_property(record, expr.field)
            if prop is not None:
                expr.is_property_access = True
                # Construct a TpyMethodCall so codegen can delegate to normal method path
                getter_call = TpyMethodCall(obj=expr.obj, method=expr.field, args=[])
                getter_call.resolved_function_info = prop.getter
                expr.property_getter_call = getter_call
                prop_type = prop.type
                if type_subst:
                    prop_type = self.type_ops.substitute_type_params(prop_type, type_subst)
                return prop_type
            # Class constant fallback for instance-side reads (`obj.X`,
            # `self.X`); codegen emits the declaring class's qualified name
            # regardless of which class the user accessed through.
            return self._lookup_class_constant_owner(record, expr)

        if isinstance(typ, TypeParamRef):
            bound = self.type_ops.get_type_param_bound(typ.name)
            if bound is not None and is_protocol_type(bound):
                protocol_info = protocol_info_of(bound)
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

    def _resolve_nested_type_access(self, parent_name: str, field: str,
                                       expr: TpyFieldAccess) -> TpyType | None:
        """Check if parent_name.field is a nested enum or record. Returns type or None."""
        dotted = f"{parent_name}.{field}"
        nested_enum = self.ctx.registry.get_enum(dotted)
        if nested_enum is not None:
            return nested_enum
        nested_record = self.ctx.registry.get_record(dotted)
        if nested_record is not None:
            return NominalType(dotted)
        return None

    def _resolve_nested_chain(self, expr: TpyFieldAccess) -> tuple[str, TpyType] | None:
        """Resolve a chain of field accesses to a nested type (e.g., Outer.Mid.Inner).

        Returns (dotted_name, resolved_type) or None if not a nested type chain.
        """
        if isinstance(expr.obj, TpyName):
            if self.ctx.func.current_ns:
                binding = self.ctx.func.current_ns.lookup(expr.obj.name)
                if binding and binding.kind in (BindingKind.RECORD, BindingKind.IMPORTED_NAME):
                    dotted = f"{expr.obj.name}.{expr.field}"
                    nested_enum = self.ctx.registry.get_enum(dotted)
                    if nested_enum is not None:
                        return (dotted, nested_enum)
                    nested_record = self.ctx.registry.get_record(dotted)
                    if nested_record is not None:
                        return (dotted, NominalType(dotted))
        elif isinstance(expr.obj, TpyFieldAccess):
            parent = self._resolve_nested_chain(expr.obj)
            if parent is not None:
                dotted_parent, parent_type = parent
                if isinstance(parent_type, NominalType):
                    dotted = f"{dotted_parent}.{expr.field}"
                    nested_enum = self.ctx.registry.get_enum(dotted)
                    if nested_enum is not None:
                        return (dotted, nested_enum)
                    nested_record = self.ctx.registry.get_record(dotted)
                    if nested_record is not None:
                        return (dotted, NominalType(dotted))
        return None

    def _lookup_class_constant_owner(
        self, record: RecordInfo, expr: TpyFieldAccess,
    ) -> TpyType | None:
        """Resolve `expr.field` against `record.class_constants`, walking
        `mro_ancestors` to the declaring ancestor when not declared directly.
        Sets `expr.class_constant_owner` to the *declaring* record so codegen
        emits `<declaring_qname>::<member>` regardless of the access path.
        """
        owner = self.ctx.registry.find_class_constant_owner(record, expr.field)
        if owner is None:
            return None
        # Multi-base same-name ambiguity: mirror the instance-field check in
        # `_try_find_field` so C3 linearization doesn't silently pick one
        # branch when more than one parent contributes the constant.
        if owner is not record and len(record.parents) > 1:
            branches = self.protocols.find_class_constant_parent_branches(record, expr.field)
            if len(branches) > 1:
                names = ", ".join(branches)
                first, second = branches[0], branches[1]
                raise self.ctx.error(
                    f"Ambiguous class constant '{expr.field}' inherited from {{{names}}} "
                    f"in '{record.name}'; use '{first}.{expr.field}' "
                    f"or '{second}.{expr.field}'",
                    expr,
                )
        # Phase 9: a class constant on a generic class is per-instantiation
        # in C++ (`C<T>::X`), so codegen needs the concrete (or template-scope)
        # type args at the access site. We get them from the receiver's type
        # only when the receiver is a direct instance of the generic owner.
        # Inheritance with fixed type-args (`class Child(C[Int32]): pass;
        # obj: Child; obj.X`) loses the args at the receiver-record level
        # and is deferred -- reject for now with a clear hint.
        if owner.type_params and owner is not record:
            raise self.ctx.error(
                f"class constant '{expr.field}' on generic class "
                f"'{owner.name}' cannot be accessed through subclass "
                f"'{record.name}'; access through an instance of "
                f"'{owner.name}[...]' instead",
                expr,
            )
        expr.class_constant_owner = owner
        return owner.class_constants[expr.field].type

    def _try_class_constant_access(
        self, expr: TpyFieldAccess, binding: NameBinding,
    ) -> TpyType | None:
        """Resolve `<RecordName>.<field>` against the record's class_constants
        (with MRO walk via `_lookup_class_constant_owner`).
        """
        assert isinstance(expr.obj, TpyName)
        record_info: RecordInfo | None = None
        if binding.kind == BindingKind.RECORD:
            record_info = self.ctx.registry.get_record(expr.obj.name)
        elif binding.kind == BindingKind.IMPORTED_NAME:
            import_info = self.ctx.imported_names.get(expr.obj.name)
            if import_info:
                record_info = self.ctx.registry.find_record_by_qname(
                    f"{import_info[0]}.{import_info[1]}")
        if record_info is None:
            return None
        return self._class_constant_access_on_record(expr, record_info)

    def _class_constant_access_on_record(
        self, expr: TpyFieldAccess, record_info: RecordInfo,
    ) -> TpyType | None:
        """Class-constant resolution given a pre-resolved record. Shared by the
        bare-name path (`Foo.CONST`) and the module-qualified path
        (`m.Foo.CONST`)."""
        # Phase 9: bare-class access on a generic class can't render the
        # parameterized qname (no type args at the access site), so reject
        # and point the user at instance access. `Class[Int32].X` syntax
        # for class-level access on a parameterized generic is not yet
        # supported either.
        if record_info.type_params and expr.field in record_info.class_constants:
            raise self.ctx.error(
                f"cannot access class constant '{expr.field}' on generic "
                f"class '{record_info.name}' through the bare class name; "
                f"access through an instance instead "
                f"(e.g. `{record_info.name}[T_args]().{expr.field}`)",
                expr,
            )
        return self._lookup_class_constant_owner(record_info, expr)

    def _try_unbound_self_field_access(
        self, expr: TpyFieldAccess, binding: NameBinding,
    ) -> TpyType | None:
        """Handle `BaseN.field` accessing an ancestor subobject's field.

        Returns the resolved field type, or None when the access is not an
        unbound-self form (not inside an instance method, or BaseN doesn't
        resolve to a known record) so the caller can fall through to the
        regular field-access path.
        """
        assert isinstance(expr.obj, TpyName)
        current_rec_parse = self.ctx.record_ctx.record
        current_fn = self.ctx.func.current_function
        if (current_rec_parse is None
                or current_fn is None
                or not isinstance(current_fn, TpyFunction)
                or not current_fn.is_method
                or current_fn.is_staticmethod):
            return None
        current_rec = self.ctx.registry.get_record(current_rec_parse.name)
        if current_rec is None:
            return None

        record_info = None
        if binding.kind == BindingKind.RECORD:
            record_info = self.ctx.registry.get_record(expr.obj.name)
        elif binding.kind == BindingKind.IMPORTED_NAME:
            import_info = self.ctx.imported_names.get(expr.obj.name)
            if import_info:
                record_info = self.ctx.registry.find_record_by_qname(
                    f"{import_info[0]}.{import_info[1]}")
        if record_info is None:
            return None

        # Walk BaseN's own MRO so `B.foo` resolves a field B inherits from
        # its own parent, matching Python's `B.foo` semantics.
        field_info = self.protocols.lookup_record_field(record_info, expr.field)
        if field_info is None:
            return None

        # BaseN has the field but isn't an ancestor: the user clearly meant
        # unbound-self, so emit a targeted error rather than letting the
        # caller fall through to a generic "can't treat class as value".
        if not self.ctx.registry.is_subclass_of_record(current_rec, record_info):
            raise self.ctx.error(
                f"'{record_info.name}' is not an ancestor of '{current_rec.name}'; "
                f"cannot access '{record_info.name}.{expr.field}' here",
                expr,
            )

        parent_type, type_subst = self.protocols.resolve_ancestor_instantiation(
            current_rec, record_info)
        field_type = field_info.type
        if type_subst:
            field_type = self.type_ops.substitute_type_params(field_type, type_subst)

        # Readonly self propagates into non-value reads so writes through
        # the result are rejected and references come back const.
        if current_fn.is_readonly and not field_type.is_value_type():
            if isinstance(field_type, PtrType) and not field_type.is_readonly:
                field_type = field_type.as_const()
            elif is_span(field_type) and not is_readonly_span(field_type):
                field_type = span_as_const(field_type)
            elif not isinstance(field_type, ReadonlyType):
                field_type = ReadonlyType(field_type)

        expr.unbound_self_parent_type = parent_type
        return make_ref(field_type)

    def _analyze_field_access(self, expr: TpyFieldAccess) -> TpyType:
        """Analyze a field access."""
        # Check for module variable access (e.g., sys.argv)
        if isinstance(expr.obj, TpyName):
            if self.ctx.func.current_ns:
                binding = self.ctx.func.current_ns.lookup(expr.obj.name)
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
                    if expr.field in enum_info_of(enum_type).members:
                        return enum_type
                    raise self.ctx.error(
                        f"Enum '{enum_type.name}' has no member '{expr.field}'", expr)

                # Nested type access on a record: Container.Kind, Container.Inner
                # Also check IMPORTED_NAME that resolves to a record (cross-module)
                if binding and binding.kind in (BindingKind.RECORD, BindingKind.IMPORTED_NAME):
                    nested = self._resolve_nested_type_access(expr.obj.name, expr.field, expr)
                    if nested is not None:
                        return nested
                    unbound = self._try_unbound_self_field_access(expr, binding)
                    if unbound is not None:
                        return unbound
                    class_const = self._try_class_constant_access(expr, binding)
                    if class_const is not None:
                        return class_const

        # Module-qualified class member access: m.Foo.CONST, pkg.sub.Foo.CONST.
        # Mirrors the method-call dispatcher's _try_resolve_module_qualified_class.
        # Codegen reads class_constant_owner (set inside the helper) to emit the
        # full C++ qname; the syntactic receiver shape doesn't matter from there.
        if isinstance(expr.obj, TpyFieldAccess):
            resolved = self.methods._try_resolve_module_qualified_class(expr.obj)
            if resolved is not None:
                _module_name, _class_short, record_info = resolved
                class_const = self._class_constant_access_on_record(expr, record_info)
                if class_const is not None:
                    return class_const

        # `import pkg.sub` + `pkg.sub.X`: walk the chain to recover a dotted
        # module name and treat the leaf as a variable on that module.
        # Mirrors the method-call form that already works via
        # MethodAnalyzer._try_resolve_dotted_module.
        if isinstance(expr.obj, TpyFieldAccess):
            dotted_name = self.methods._try_resolve_dotted_module(expr.obj)
            if dotted_name is not None:
                module_info = self.ctx.registry.get_module(dotted_name)
                if module_info is not None and expr.field in module_info.variables:
                    expr.module_var_access = (dotted_name, expr.field)
                    return module_info.variables[expr.field].type

        # Handle chained nested type access: Outer.Mid.Inner.field
        if isinstance(expr.obj, TpyFieldAccess):
            chain = self._resolve_nested_chain(expr.obj)
            if chain is not None:
                # chain is a (dotted_name, type) for the intermediate nested type
                dotted_name, chain_type = chain
                if is_enum_type(chain_type):
                    if expr.field in enum_info_of(chain_type).members:
                        return chain_type
                    if expr.field in ("name", "value"):
                        pass  # fall through to normal field access
                    else:
                        raise self.ctx.error(
                            f"Enum '{chain_type.name}' has no member '{expr.field}'", expr)
                elif isinstance(chain_type, NominalType):
                    # Try further nesting
                    nested = self._resolve_nested_type_access(dotted_name, expr.field, expr)
                    if nested is not None:
                        return nested

        obj_type = self.analyze_expr(expr.obj)

        # Unwrap transparent wrappers
        is_readonly_obj = isinstance(obj_type, ReadonlyType)
        actual_type = unwrap_ref_type(obj_type)
        if isinstance(actual_type, ReadonlyType):
            actual_type = actual_type.wrapped
        if isinstance(actual_type, OwnType):
            actual_type = actual_type.wrapped
        if isinstance(actual_type, OptionalType):
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
        if is_enum_type(actual_type):
            if expr.field == "name":
                return STR
            elif expr.field == "value":
                return enum_info_of(actual_type).underlying_type
            raise self.ctx.error(
                f"Enum value of type '{actual_type.name}' has no attribute '{expr.field}'. "
                f"Use '{actual_type.name}.{expr.field}' to access enum members", expr)

        # Deref chain loop -- resolves through Ptr (mutable and readonly) and any Deref[T] type
        current_type = actual_type
        deref_depth = 0
        while deref_depth <= 8:
            result = self._try_find_field(current_type, expr)
            if result is not None:
                # Class constants emit `<owner_qname>::<member>` ignoring
                # `obj`, so deref state, ptr-non-null narrowing,
                # ownership-from-self, and field-path narrowing don't apply.
                if expr.class_constant_owner is not None:
                    return make_ref(result)
                expr.deref_depth = deref_depth
                if deref_depth > 0 and isinstance(actual_type, PtrType):
                    obj_key = _expr_to_narrowing_key(expr.obj)
                    if obj_key is not None:
                        if obj_key in self.ctx.func.non_null_ptr_vars:
                            expr.ptr_non_null = True
                        if expr.loc:
                            self.ctx.ptr_deref_facts[
                                (expr.loc.line, obj_key)
                            ] = expr.ptr_non_null
                        # Post-access narrowing: a successful deref here means
                        # the pointer is non-null for *subsequent statements*;
                        # queued for flush at statement boundary rather than
                        # applied immediately to avoid unsafe elision between
                        # unspecified-order siblings in the same expression.
                        self.ctx.func.pending_non_null_ptr_vars.add(obj_key)
                # Propagate readonly: accessing a non-value field through a
                # readonly reference yields a readonly result.
                # Ptr[T] fields become Ptr[readonly[T]], Span[T] -> Span[readonly[T]].
                if is_readonly_obj:
                    if isinstance(result, PtrType) and not result.is_readonly:
                        result = result.as_const()
                    elif is_span(result) and not is_readonly_span(result):
                        result = span_as_const(result)
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
                    narrowed = self.ctx.func.narrowed_types.get(field_key)
                    if narrowed is not None:
                        result = narrowed
                return make_ref(result)

            deref_target = self.get_deref_target_type(
                current_type, is_readonly=is_readonly_obj)
            if deref_target is None:
                break
            # __deref__() may return readonly[T]; unwrap and propagate
            # readonly so field access enforces const semantics.
            if isinstance(deref_target, ReadonlyType):
                is_readonly_obj = True
                deref_target = deref_target.wrapped
            # Deref-view narrowing: a field declared only on the narrowed
            # subclass resolves through the cast (see the method-call path).
            nsub = deref_view_narrowed(self.ctx, expr.obj, deref_target)
            if nsub is not None:
                expr.deref_narrowed_to = nsub
                current_type = nsub
            else:
                current_type = deref_target
            deref_depth += 1

        # D16 dynamic-attribute fallback: if the receiver is a record with
        # __getattr__ reachable via MRO, route the access through the dunder
        # as a synthesized method call (parallel to property routing). Fires
        # only after the static-resolution + deref loop has failed.
        if isinstance(actual_type, NominalType) and actual_type.is_record:
            dyn_result = self._try_dyn_getattr(actual_type, expr)
            if dyn_result is not None:
                return make_ref(dyn_result)
            raise self.ctx.error(f"Record '{actual_type.name}' has no field '{expr.field}'", expr)
        raise self.ctx.error(f"Cannot access field '{expr.field}' on type {obj_type}", expr)

    def _try_dyn_getattr(self, typ: NominalType, expr: TpyFieldAccess) -> TpyType | None:
        """D16 Phase 1: try routing `obj.field` through __getattr__.

        Returns the dunder's substituted return type (and stashes the
        synthesized TpyMethodCall on `expr.dyn_getattr_call`) if the
        receiver's class has `__getattr__` via MRO; None otherwise.
        """
        record = self.ctx.registry.get_record_for_type(typ)
        if record is None:
            return None
        overloads, _ = self.protocols.lookup_record_method_overloads(
            record, "__getattr__")
        if not overloads:
            return None
        # Delegate to method-call analysis so @readonly enforcement, mutation
        # propagation, and call-edge recording all fire uniformly. Returns
        # the (substituted) dunder return type. Bypassing this path was the
        # original D16 v1 review gap.
        getter_call = TpyMethodCall(
            obj=expr.obj,
            method="__getattr__",
            args=[TpyStrLiteral(value=expr.field)],
            loc=expr.loc,
        )
        ret_type = self.analyze_expr(getter_call)
        expr.dyn_getattr_call = getter_call
        return ret_type

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
            if not is_body_like_scope(self.ctx.func.current_function):
                raise self.ctx.error("Empty array literal requires explicit type annotation", expr)
            # Empty list with no annotation -- create PendingListType with unknown
            # element type. The element type will be inferred from subsequent usage
            # (e.g. .append(v), xs[i] = v, param context, return context).

            # Reuse an existing PendingListType for this expr if one was
            # already created. analyze_expr can be called multiple times for
            # the same arg expr (pre-overload arg-type collection, then post-
            # overload _typecheck_call_args). Without this cache, each call
            # mints a new literal_id and a new ListLiteralInfo added to
            # pending_resolutions -- coercion writes to one, but the resolver
            # still fails on the stale one.
            cached = self.ctx.get_expr_type(expr)
            if isinstance(cached, PendingListType) and cached.size == 0:
                return cached
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
            self.ctx.func.pending_resolutions.append(literal_id)
            typ = PendingListType(UNKNOWN_ELEMENT, 0, literal_id)
            self.ctx.set_expr_type(expr, typ)
            return typ

        # Analyze all elements, propagating expected type as hint when available
        if expected_elem is not None:
            elem_types = [self.analyze_expr_with_hint(e, expected_elem) for e in expr.elements]
        else:
            elem_types = [self.analyze_expr(e) for e in expr.elements]

        # Strip OwnType wrappers -- _apply_own_wrapper marks non-last-use
        # refs as Own[T], but element type compatibility must compare the
        # underlying types (codegen handles the move/copy distinction).
        elem_types = [unwrap_own(t) for t in elem_types]

        if expected_elem is not None:
            # Contextual mode: check each element against expected element type
            first_type = expected_elem
            for i, elem_type in enumerate(elem_types, 1):
                if elem_type == expected_elem:
                    continue
                # Subclass coercion excluded: storing Child in list[Base] silently
                # slices objects (same invariance as dict/set). A covariant-generic
                # wrapper upcast (Box[Dog] -> Box[Pet]) is exempt -- it's a
                # representation-preserving converting move, not slicing -- and
                # falls through to check_type_compatible below (which the append
                # path already uses).
                if (isinstance(elem_type, NominalType) and elem_type.is_user_record
                        and isinstance(expected_elem, NominalType) and expected_elem.is_user_record
                        and not self.compat.is_covariant_generic_upcast(elem_type, expected_elem)):
                    raise self.ctx.error(
                        f"List literal element {i} has type {elem_type}, "
                        f"incompatible with annotated element type {expected_elem}", expr
                    )
                try:
                    self.compat.check_type_compatible(
                        elem_type, expected_elem,
                        f"array literal element {i}",
                        expr.loc,
                        source_expr=expr.elements[i - 1],
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
                # Literal-aware unification (int/float literals, tuples of literals)
                unified = self._unify_literal_types(first_type, elem_type)
                if unified is not None:
                    first_type = unified
                    continue
                # Nested lists with IntLiteralType elements are compatible
                if (is_list(first_type) and is_list(elem_type) and
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
        if self.ctx.func.current_function is None:
            return make_list(first_type)

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
        self.ctx.func.pending_resolutions.append(literal_id)

        return PendingListType(first_type, size, literal_id)


    # -- await -----------------------------------------------------------

    def _analyze_await(self, expr: TpyAwait) -> TpyType:
        """Analyze `await x`.

        v1 (Commit 3 of PR 3) supports statically-resolvable awaits only:
        the operand must be a direct call to a known async def. Type-erased
        awaits (Awaitable[T] params, Task[T], unions) lower via
        AsyncFrameBase<T> in PR 4.

        The await expression's type is the awaited async def's declared
        return type. The compiler tags the TpyAwait node with
        `awaited_async_func` so codegen can resolve the sub-coroutine
        struct and emit the in-frame `std::optional<__SubCoro>` field.
        """
        cur = self.ctx.func.current_function
        if not (isinstance(cur, TpyFunction) and cur.is_async):
            raise self.ctx.error(
                "'await' is only allowed inside an `async def` function body; "
                "use asyncio.run(coro) at the top level to drive a coroutine",
                expr)
        # Two supported v1 shapes:
        # 1. Inline: operand is a direct call to a known async def.
        # 2. Erased: operand has type Task[T] (heap-allocated frame).
        operand = expr.value
        async_fi = self._resolve_call_to_async_def(operand)
        if async_fi is not None:
            # Recursively analyze the operand call (validates arg types and,
            # for generic async defs, infers and substitutes type args into
            # the operand's `resolved_function_info`).
            self.analyze_expr(operand)
            expr.awaited_async_func_name = async_fi.name
            # Prefer the operand's substituted FunctionInfo's return type;
            # for generic async defs, `async_fi.return_type` from the
            # registry still carries unsubstituted TypeParamRefs.
            resolved_fi = getattr(operand, "resolved_function_info", None)
            source_fi = resolved_fi if resolved_fi is not None else async_fi
            # Frame Send/Sync: the sub-coro is stored inline in this frame
            self.ctx.func.current_awaited_subframes.append(async_fi)
            return self._unwrap_awaitable_return(source_fi.return_type)

        # Type-erased path: analyze operand. Supported v1 erased forms:
        #   - tpy.Task[T]            (heap-erased coroutine frame)
        #   - asyncio.Future[T]      (manual-completion awaitable)
        #   - any record type with `poll(self, w: Waker) -> Poll[T]`
        #     (structural Awaitable -- supports user-written awaitables
        #     alongside hand-written awaiter types).
        operand_type = self.analyze_expr(operand)

        # Method-call shape: `await obj.method()`. The free-function probe
        # above only matches bare-name calls; method calls land here. After
        # analyze_expr, the method call carries `resolved_function_info`;
        # treat an async one like the inline shape.
        if isinstance(operand, TpyMethodCall):
            mfi = operand.resolved_function_info
            if mfi is not None and mfi.is_async:
                # Receiver must be a stable lvalue: the coro captures it as
                # `<Class>&` across polls, so a temporary would dangle at
                # the emplace expression's semicolon. Mirrors
                # `AsyncCoroCodegen._is_stable_lvalue` in codegen.
                if not is_stable_address_lvalue(operand.obj):
                    raise self.ctx.error(
                        "receiver of an awaited async method must be "
                        "a stable lvalue (a local, parameter, or "
                        "field chain rooted at one) -- the coro "
                        "captures it by reference across "
                        "suspensions, so a temporary would dangle. "
                        "Bind the receiver to a local first: "
                        "`r = <expr>; await r.method(...)`",
                        operand)
                expr.awaited_async_func_name = mfi.name
                owner_type = self._method_call_receiver_type(operand)
                if owner_type is not None:
                    expr.awaited_method_owner_type = owner_type
                if mfi.return_type is not None:
                    self.ctx.func.current_awaited_subframes.append(mfi)
                    return self._unwrap_awaitable_return(mfi.return_type)
        # An Own[Task[T]] rvalue (e.g. `await asyncio.create_task(...)`)
        # is a valid await operand -- strip the Own[] before structural
        # matching so the inner Task[T] / Awaitable conformance check fires.
        unwrapped = unwrap_own(unwrap_ref_type(operand_type))
        if isinstance(unwrapped, NominalType):
            inner, poll_is_readonly = self._extract_awaitable_inner(unwrapped)
            if inner is not None:
                if isinstance(inner, TpyType):
                    expr.awaited_task_inner = inner
                    # Erased awaitable (Task / Future / structural): sema
                    # cannot classify the stored frame -- non-Send.
                    self.ctx.func.current_awaited_subframes.append(None)
                    # `await x` polls `x.__poll__(waker)`; a non-readonly
                    # __poll__ mutates the awaitable, so awaiting a durable
                    # operand mutates its root. Mark it -- otherwise a method
                    # whose only self-touch is `await self` / `await self.f`
                    # is mis-inferred @readonly and captures a const receiver
                    # the mutating __poll__ can't use.
                    if not poll_is_readonly:
                        self._mark_await_operand_mutated(operand)
                    return inner
        raise self.ctx.error(
            "await operand must be a direct call to an async def, a "
            "Task[T] / Future[T], or a value of a type with a "
            "`__poll__(self, waker: Waker) -> Own[Poll[T]]` method",
            expr)

    def _mark_await_operand_mutated(self, operand) -> None:
        """Mark the durable root of an awaited operand mutated.

        Called when the awaitable's `__poll__` is non-readonly. Mirrors the
        non-readonly method-call receiver marking in `MethodAnalyzer`: the
        await is, for mutation purposes, an `operand.__poll__(waker)` call.
        A rvalue operand (fresh coro from `await f()`) has no durable root
        and is left alone.
        """
        from .statements import _root_name_of_expr
        obj_root = _root_name_of_expr(operand)
        if obj_root is None:
            return
        self.ctx.mark_loop_var_mutated(obj_root)
        self.ctx.mark_param_mutated(obj_root)
        storage = self.ctx.func.borrow_tracker.effective_storage(obj_root)
        self.ctx.mark_all_view_borrowers_mutated(storage)

    def _unwrap_awaitable_return(self, ret_type: 'TpyType') -> 'TpyType':
        """Strip `Awaitable[T]` / `Cancellable[T]` wrapping from an async
        def's return type. Cancellable is the post-registration shape of
        every async-def call result; Awaitable is the shape user types
        with just `__poll__` declare. Both unwrap to `T` for `await`."""
        ret = unwrap_ref_type(ret_type)
        if (isinstance(ret, NominalType)
                and ret.qualified_name() in (qnames.AWAITABLE, qnames.CANCELLABLE)
                and len(ret.type_args) == 1):
            return ret.type_args[0]
        return ret

    def _extract_awaitable_inner(self, typ) -> 'tuple[TpyType | None, bool]':
        """Return `(T, poll_is_readonly)` if `typ` conforms to Awaitable[T],
        else `(None, True)`.

        Structural: any record with `__poll__(self, waker: Waker) -> Own[Poll[T]]`.
        Covers tpy.Task[T] (qname `tpy.Task` -> the @builtin_type stub in
        asyncio._executor), user-defined Future[T] / Event-like types, and
        any other record that satisfies the Awaitable protocol. The Own
        wrapper is required because Poll[T] is @nocopy (v1.2 step 5).

        `poll_is_readonly` is the chosen `__poll__` overload's readonly-ness:
        a non-readonly `__poll__` mutates the awaitable, so awaiting a durable
        operand (`await self` / `await self.field`) mutates the operand's root.
        """
        from ..typesys import NominalType, TpyType as _TpyType
        # Structural: look up the record and check for a __poll__ method
        # whose signature matches Awaitable[T].
        record_info = self.ctx.registry.get_record_for_type(typ)
        if record_info is None:
            return (None, True)
        poll_overloads = record_info.get_method_overloads("__poll__")
        if not poll_overloads:
            return (None, True)
        # Pick the first overload whose return type is Poll[T] for some T.
        # Substitute the record's class-level type params with typ.type_args
        # so `Future[Int32]` returns Int32, not the type-var T.
        from ..typesys import unwrap_ref_type
        type_subst: dict[str, _TpyType] = {}
        if record_info.type_params and len(typ.type_args) == len(record_info.type_params):
            for tp, arg in zip(record_info.type_params, typ.type_args):
                if isinstance(arg, _TpyType):
                    type_subst[tp] = arg
        for fi in poll_overloads:
            ret = fi.return_type
            if ret is None:
                continue
            # Poll[T] is @nocopy, so a bare `-> Poll[T]` is a reference
            # return -- skip non-Own returns so the user gets the "no
            # __poll__ method" diagnostic (which prints the correct
            # `Own[Poll[T]]` signature) instead of a C++ build failure.
            ret_outer = unwrap_ref_type(ret)
            if not isinstance(ret_outer, OwnType):
                continue
            ret = unwrap_own(ret_outer)
            if (isinstance(ret, NominalType)
                    and ret._module_qname == qnames.POLL
                    and len(ret.type_args) == 1):
                # Recursively substitute T -> typ.type_args[i] -- handles
                # both bare TypeParamRef and nested shapes like list[T],
                # tuple[T, U], etc.
                return (_substitute_type_params(ret.type_args[0], type_subst),
                        bool(fi.is_readonly))
        return (None, True)

    def _method_call_receiver_type(self, call) -> 'NominalType | None':
        """For a TpyMethodCall whose receiver resolves to a known record,
        return the receiver's NominalType (carrying class-level
        type_args). Used to name and qualify async-method coro structs
        as `__coro_<Record>_<method>` plus a `<owner_type_args>` suffix
        for receivers with non-empty class-level type args.
        """
        recv_type = self.ctx.get_expr_type(call.obj)
        if recv_type is None:
            return None
        inner = unwrap_own(unwrap_ref_type(recv_type))
        if isinstance(inner, NominalType):
            return inner
        return None

    def _resolve_call_to_async_def(self, operand) -> 'FunctionInfo | None':
        """If operand is a direct TpyCall whose target is a known async def,
        return its FunctionInfo. Otherwise return None.

        Free-function call shape only -- method calls (`await obj.m()`)
        are detected after `analyze_expr` populates the
        `TpyMethodCall.resolved_function_info` field; that branch lives
        in `analyze_await` above.
        """
        from ..parse.nodes import TpyCall
        if not isinstance(operand, TpyCall):
            return None
        func_name = operand.maybe_func_name
        if not func_name:
            return None
        overloads = self.ctx.registry.get_function(func_name)
        if not overloads:
            return None
        # Async defs do not participate in @overload, so a single match is OK.
        for fi in overloads:
            if fi.is_async:
                return fi
        return None

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
        target_scope = self.ctx.func.current_scope
        levels = self.ctx.in_comprehension
        while levels > 0 and target_scope.parent is not None:
            target_scope = target_scope.parent
            levels -= 1

        existing = target_scope.lookup(name)
        if existing is not None:
            # Reassignment via walrus. Non-value (and pointer-repr-tuple) locals
            # use a storage form (T* / std::optional<T> / borrow slot) the walrus
            # binding path can't rebind in place without the var-decl rebind
            # machinery, so reject them rather than emit a conflicting
            # redeclaration; value-typed locals route through the shared
            # reassignment-compat check (view tracking + coerce).
            inner_existing = unwrap_readonly(existing)
            borrow_tuple_existing = (isinstance(inner_existing, TupleType)
                                     and inner_existing.has_pointer_repr_element())
            if not inner_existing.is_value_type() or borrow_tuple_existing:
                raise self.ctx.error(
                    f"walrus reassignment of non-value local '{name}' is not "
                    f"supported yet; use a separate assignment statement",
                    expr)
            _, expr.value = self.compat.coerce_reassignment(
                name, existing, value_type, expr.value, expr)
            resolved = existing
            result_type = existing
        else:
            # New binding
            target_scope.define(name, resolved)
            if self.ctx.func.current_ns:
                self.ctx.func.current_ns.bind_variable(name, resolved)
            result_type = value_type

        self.ctx.func.definitely_assigned.add(name)
        self.ctx.func.rvalue_vars.add(name)
        if name not in self.ctx.func.var_scope_depth:
            self.ctx.func.var_scope_depth[name] = target_scope.depth

        # Mirror the VarDecl tuple facts: a walrus-bound pointer-repr tuple
        # carries the same owns-fresh hazard as `t = (1, Box(5))`, and an
        # lvalue-sourced binding ((t := items[0])) aliases its storage, so
        # the borrow must be registered for the return-root gate and the
        # deferred mutation-marking to see it. A rebind drops/retargets the
        # old chain first, like the VarDecl path.
        # Peel Own for the tuple bookkeeping: a walrus bound from an
        # owning-tuple call carries the same facts as the VarDecl binding,
        # whose inferred local type is already Own-stripped.
        facts_type = unwrap_own(unwrap_readonly(resolved))
        self.compat.update_tuple_member_local_facts(name, facts_type,
                                                    expr.value)
        res_bare = unwrap_readonly(facts_type)
        is_borrow_tuple = (isinstance(res_bare, TupleType)
                           and res_bare.has_pointer_repr_element())
        if is_borrow_tuple:
            bt = self.ctx.func.borrow_tracker
            bt.retarget_storage_borrows(name)
            bt.remove_borrower(name)
            val_inner = (expr.value.expr if isinstance(expr.value, TpyCoerce)
                         else expr.value)
            if isinstance(val_inner, (TpyName, TpySubscript, TpyFieldAccess)):
                register_binding_borrow(self.ctx, name, expr.value)
        # An alias of an ephemeral borrow is the same stale-slot borrow under
        # another name (mirrors the VarDecl _update_ephemeral_alias_fact).
        eph = self.ctx.func.ephemeral_borrow_vars
        if ((not resolved.is_value_type() or is_borrow_tuple)
                and ephemeral_borrow_root(eph, expr.value) is not None):
            eph.add(name)
        else:
            eph.discard(name)

        return result_type

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
        saved_narrowed = dict(self.ctx.func.narrowed_types)

        self.ctx.func.narrowed_types.update(then_facts)
        if type_hint is not None:
            then_type = self.analyze_expr_with_hint(expr.then_expr, type_hint)
        else:
            then_type = self.analyze_expr(expr.then_expr)

        self.ctx.func.narrowed_types = dict(saved_narrowed)
        self.ctx.func.narrowed_types.update(else_facts)
        if type_hint is not None:
            else_type = self.analyze_expr_with_hint(expr.else_expr, type_hint)
        else:
            else_type = self.analyze_expr(expr.else_expr)

        self.ctx.func.narrowed_types = saved_narrowed

        # Strip Ref/Own from branch types -- these are provenance qualifiers,
        # not part of the result type.  The ternary produces a value.
        then_type = unwrap_ref_type(then_type)
        if isinstance(then_type, OwnType):
            then_type = then_type.wrapped
        else_type = unwrap_ref_type(else_type)
        if isinstance(else_type, OwnType):
            else_type = else_type.wrapped

        common = self._ternary_common_type(expr, then_type, else_type,
                                           widen_numeric_types)

        if is_list(common):
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
            if is_integer_type(e) or is_float_type(e):
                return e
            t = self.ctx.default_int_for_literal(t, expr.then_expr)
        if isinstance(e, IntLiteralType):
            if is_integer_type(t) or is_float_type(t):
                return t
            e = self.ctx.default_int_for_literal(e, expr.else_expr)

        # FloatLiteral resolution: adapts to the concrete float type in context
        if isinstance(t, FloatLiteralType) and isinstance(e, FloatLiteralType):
            return FLOAT
        if isinstance(t, FloatLiteralType):
            if is_float_type(e):
                return e
            t = FLOAT
        if isinstance(e, FloatLiteralType):
            if is_float_type(t):
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

    def _resolve_literals_with_hint(self, t: TpyType, hint: TpyType | None) -> TpyType:
        """Resolve IntLiteralType / FloatLiteralType inside t using hint as a
        structural guide. Recurses into TupleType. Falls back to default_int /
        FLOAT when hint doesn't match the literal's family.

        Mirrors the asymmetric behavior of the bare-literal path: integer
        literals adopt the hint when present (compat-checked downstream),
        float literals only adopt the hint when it's a float type.
        """
        if isinstance(t, IntLiteralType):
            return hint if hint is not None else self.ctx.default_int_for_literal(t)
        if isinstance(t, FloatLiteralType):
            return hint if is_float_type(hint) else FLOAT
        if isinstance(t, TupleType):
            if isinstance(hint, TupleType) and len(t.element_types) == len(hint.element_types):
                elems = tuple(
                    self._resolve_literals_with_hint(et, ht)
                    for et, ht in zip(t.element_types, hint.element_types)
                )
            else:
                elems = tuple(
                    self._resolve_literals_with_hint(et, None)
                    for et in t.element_types
                )
            return TupleType(elems)
        return t

    def _unify_literal_types(self, a: TpyType, b: TpyType) -> TpyType | None:
        """Unify two dict/set literal element types, treating IntLiteralType /
        FloatLiteralType as compatible with their concrete equivalents and
        recursing into TupleType. Returns the unified type or None if they
        cannot be unified.
        """
        if a == b:
            return a
        if isinstance(a, IntLiteralType) and isinstance(b, IntLiteralType):
            return a
        if isinstance(a, IntLiteralType) and is_integer_type(b):
            return b
        if isinstance(b, IntLiteralType) and is_integer_type(a):
            return a
        if isinstance(a, FloatLiteralType) and isinstance(b, FloatLiteralType):
            return a
        if isinstance(a, FloatLiteralType) and is_float_type(b):
            return b
        if isinstance(b, FloatLiteralType) and is_float_type(a):
            return a
        if isinstance(a, TupleType) and isinstance(b, TupleType):
            if len(a.element_types) != len(b.element_types):
                return None
            unified: list[TpyType] = []
            for ea, eb in zip(a.element_types, b.element_types):
                u = self._unify_literal_types(ea, eb)
                if u is None:
                    return None
                unified.append(u)
            return TupleType(tuple(unified))
        return None

    def _analyze_dict_literal(
        self, expr: TpyDictLiteral,
        expected_key: TpyType | None = None,
        expected_value: TpyType | None = None,
    ) -> TpyType:
        """Analyze a dict literal {key: value, ...}"""
        if not expr.keys:
            # Hint from LHS / param / return type pins K, V directly -- no
            # usage-based inference needed.
            if expected_key is not None and expected_value is not None:
                return make_dict(expected_key, expected_value)
            if not is_body_like_scope(self.ctx.func.current_function):
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
            self.ctx.func.pending_dict_resolutions.append(literal_id)
            return PendingDictType(UNKNOWN_ELEMENT, UNKNOWN_ELEMENT, literal_id)

        if expected_key:
            key_types = [self._analyze_and_strip(k, expected_key) for k in expr.keys]
        else:
            key_types = [self._analyze_and_strip(k) for k in expr.keys]
        if expected_value:
            value_types = [self._analyze_and_strip(v, expected_value) for v in expr.values]
        else:
            value_types = [self._analyze_and_strip(v) for v in expr.values]

        # Unify key types
        if is_union_or_optional_type(expected_key) or isinstance(expected_key, AnyType):
            key_type = expected_key
            for i, kt in enumerate(key_types, 1):
                if kt == expected_key:
                    continue
                try:
                    self.compat.check_type_compatible(
                        kt, expected_key, f"dict literal key {i}", expr.loc,
                        source_expr=expr.keys[i - 1])
                except SemanticError:
                    raise self.ctx.error(
                        f"Dict literal key {i} has type {kt}, "
                        f"incompatible with annotated key type {expected_key}", expr)
        else:
            key_type = key_types[0]
            for i, kt in enumerate(key_types[1:], 2):
                unified = self._unify_literal_types(key_type, kt)
                if unified is None:
                    raise self.ctx.error(
                        f"Dict has mixed key types: key {i} is {self._user_type_name(kt)}, "
                        f"but earlier keys are {self._user_type_name(key_type)}", expr,
                    )
                key_type = unified

        # Unify value types
        if (is_union_or_optional_type(expected_value)
                or isinstance(expected_value, AnyType)
                or (expected_value is not None and expected_value.needs_wrapper())
                or (expected_value is not None and any(
                    self.compat.is_covariant_generic_upcast(vt, expected_value)
                    for vt in value_types))):
            # Annotation provides a union/optional/Any/recursive-alias wrapper --
            # each value validates against the slot independently rather than
            # against its peers (heterogeneous values are the whole point of
            # these slot types; a recursive dict alias's values are leaves or
            # nested wrappers). Same when the annotated value is a covariant-
            # generic wrapper the values upcast to (Box[Dog] -> Box[Pet]):
            # check each against the annotation so the dict builds at the
            # annotated instantiation, not the peer-unified subclass.
            value_type = expected_value
            for i, vt in enumerate(value_types, 1):
                if vt == expected_value:
                    continue
                try:
                    self.compat.check_type_compatible(
                        vt, expected_value, f"dict literal value {i}", expr.loc,
                        source_expr=expr.values[i - 1])
                except SemanticError:
                    raise self.ctx.error(
                        f"Dict literal value {i} has type {vt}, "
                        f"incompatible with annotated value type {expected_value}", expr)
        else:
            value_type = value_types[0]
            for i, vt in enumerate(value_types[1:], 2):
                unified = self._unify_literal_types(value_type, vt)
                if unified is None:
                    raise self.ctx.error(
                        f"Dict has mixed value types: value {i} is {self._user_type_name(vt)}, "
                        f"but earlier values are {self._user_type_name(value_type)}", expr,
                    )
                value_type = unified

        # Resolve any literal types (bare or nested in tuples) using the
        # annotation as a structural hint.
        key_type = self._resolve_literals_with_hint(key_type, expected_key)
        value_type = self._resolve_literals_with_hint(value_type, expected_value)
        # Container elements must be owned -- views can't be stored in a dict.
        if isinstance(key_type, PendingViewType):
            key_type = key_type.family.owned_type
        if isinstance(value_type, PendingViewType):
            value_type = value_type.family.owned_type

        self.type_ops.validate_hashable_container_elem(key_type, "dict key", expr.loc)
        return make_dict(key_type, value_type)

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
            elem_types = [self._analyze_and_strip(e, expected_elem) for e in expr.elements]
        else:
            elem_types = [self._analyze_and_strip(e) for e in expr.elements]

        # Unify element types
        if (is_union_or_optional_type(expected_elem) or isinstance(expected_elem, AnyType)
                or (expected_elem is not None and any(
                    self.compat.is_covariant_generic_upcast(et, expected_elem)
                    for et in elem_types))):
            # Annotation provides a union/optional/Any -- each element validates
            # against the slot independently rather than against its peers. Same
            # for a covariant-generic element the values upcast to (parallel to
            # the dict-value path; moot for @nocopy Box/Rc, which are rejected
            # as set elements, but kept symmetric for a hashable covariant
            # value-type element).
            elem_type = expected_elem
            for i, et in enumerate(elem_types, 1):
                if et == expected_elem:
                    continue
                try:
                    self.compat.check_type_compatible(
                        et, expected_elem, f"set literal element {i}", expr.loc,
                        source_expr=expr.elements[i - 1])
                except SemanticError:
                    raise self.ctx.error(
                        f"Set literal element {i} has type {et}, "
                        f"incompatible with annotated element type {expected_elem}", expr,
                    )
        else:
            elem_type = elem_types[0]
            for i, et in enumerate(elem_types[1:], 2):
                unified = self._unify_literal_types(elem_type, et)
                if unified is None:
                    raise self.ctx.error(
                        f"Set has mixed element types: element {i} is {self._user_type_name(et)}, "
                        f"but earlier elements are {self._user_type_name(elem_type)}", expr,
                    )
                elem_type = unified

        elem_type = self._resolve_literals_with_hint(elem_type, expected_elem)
        # Container elements must be owned -- views can't be stored in a set.
        if isinstance(elem_type, PendingViewType):
            elem_type = elem_type.family.owned_type

        self.type_ops.validate_hashable_container_elem(elem_type, "set element", expr.loc)
        return make_set(elem_type)

    def _analyze_list_repeat(self, expr: TpyListRepeat) -> TpyType:
        """Analyze a list repetition: [elements...] * count"""
        count_type = self.analyze_expr(expr.count)

        if not is_any_int_type(count_type):
            raise self.ctx.error(f"List repetition count must be an integer type, got {count_type}", expr)

        # Note: Empty list repetition [] * N is collapsed to [] in the parser

        # Analyze all elements
        elem_types = [self.analyze_expr(e) for e in expr.elements]
        first_type = elem_types[0]

        # Check all elements are compatible (similar to array literal)
        for i, elem_type in enumerate(elem_types[1:], 2):
            if isinstance(first_type, IntLiteralType) and isinstance(elem_type, IntLiteralType):
                continue
            if isinstance(elem_type, IntLiteralType) and is_integer_type(first_type):
                continue
            if isinstance(first_type, IntLiteralType) and is_integer_type(elem_type):
                first_type = elem_type
                continue
            if isinstance(first_type, FloatLiteralType) and isinstance(elem_type, FloatLiteralType):
                continue
            if isinstance(elem_type, FloatLiteralType) and is_float_type(first_type):
                continue
            if isinstance(first_type, FloatLiteralType) and is_float_type(elem_type):
                first_type = elem_type
                continue
            if first_type != elem_type:
                raise self.ctx.error(f"List repetition element {i} has type {elem_type}, expected {first_type}", expr)

        # Global context -> ListType (no deferred resolution)
        if self.ctx.func.current_function is None:
            return make_list(first_type)

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
        self.ctx.func.pending_resolutions.append(literal_id)

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
        # Genexpr borrow ABI: a bare non-value (non-readonly) element is handed
        # out by reference (val_or_ref slot), zero-copy, like a def-generator's
        # Iterator[T]. readonly / value elements keep the value (copy) slot.
        if yield_uses_borrow_slot(result_elem_type):
            # A freshly-constructed element would dangle in the borrow slot.
            # Accept borrows of the captured iteration (the loop var / its
            # fields); reject fresh constructions and point at the
            # list-comprehension form, which materializes owned storage.
            # (Genexprs have no Own[] surface, so there is no owned opt-in.)
            saved = gen.var in self.ctx.func.safe_to_return_vars
            self.ctx.func.safe_to_return_vars.add(gen.var)
            try:
                dangles = self.compat.is_dangling_return(expr.element_expr)
            finally:
                if not saved:
                    self.ctx.func.safe_to_return_vars.discard(gen.var)
            if dangles:
                raise self.ctx.error(
                    f"Cannot yield a freshly-constructed '{result_elem_type}' from a "
                    f"generator expression: it is handed out by reference and would "
                    f"dangle. Use a list comprehension '[...]' to materialize owned "
                    f"elements instead.",
                    expr.element_expr,
                )
            # Hand out a mutable borrow (the loop var aliases the live source
            # element), mirroring a def-generator borrow yield -- so the slot is
            # val_or_ref<T> (T&), not const, and consumer mutation propagates.
            gen.const_loop_var = False
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
            result_elem_type = expected_elem if is_float_type(expected_elem) else FLOAT

        if expected_elem is not None and result_elem_type != expected_elem:
            # Subclass coercion excluded: storing Child in list/set[Base] silently
            # slices objects (same invariance as container literals). Covariant-
            # generic wrapper upcasts (Box[Dog] -> Box[Pet]) are exempt -- a
            # representation-preserving converting move, not slicing -- and flow
            # through coerce_expr below.
            if (isinstance(result_elem_type, NominalType) and result_elem_type.is_user_record
                    and isinstance(expected_elem, NominalType) and expected_elem.is_user_record
                    and not self.compat.is_covariant_generic_upcast(result_elem_type, expected_elem)):
                raise self.ctx.error(
                    f"{kind.capitalize()} comprehension element has type {result_elem_type}, "
                    f"incompatible with annotated element type {expected_elem}", expr
                )
            expr.element_expr = self.compat.coerce_expr(
                expr.element_expr, result_elem_type, expected_elem,
                f"{kind} comprehension element", coercion_ctx=CoercionContext.INIT,
                target_is_storage_form=True)
            result_elem_type = expected_elem

        if kind == "set":
            self.type_ops.validate_hashable_container_elem(result_elem_type, "set element", expr.loc)

        expr.result_elem_type = result_elem_type

        if kind == "list":
            array_size = self._try_comp_array_size(expr)
            if array_size is not None and self.ctx.func.current_function is not None:
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
                self.ctx.func.pending_resolutions.append(literal_id)
                return PendingListType(result_elem_type, array_size, literal_id)

        return make_set(result_elem_type) if kind == "set" else make_list(result_elem_type)

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
        if is_array(iterable_type):
            return iterable_type.type_args[1]

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
            key_type = expected_key if is_float_type(expected_key) else FLOAT
        if isinstance(value_type, FloatLiteralType):
            value_type = expected_value if is_float_type(expected_value) else FLOAT

        if expected_key is not None and key_type != expected_key:
            expr.key_expr = self.compat.coerce_expr(
                expr.key_expr, key_type, expected_key,
                "dict comprehension key", coercion_ctx=CoercionContext.INIT,
                target_is_storage_form=True)
            key_type = expected_key
        if expected_value is not None and value_type != expected_value:
            expr.value_expr = self.compat.coerce_expr(
                expr.value_expr, value_type, expected_value,
                "dict comprehension value", coercion_ctx=CoercionContext.INIT,
                target_is_storage_form=True)
            value_type = expected_value

        self.type_ops.validate_hashable_container_elem(key_type, "dict key", expr.loc)
        expr.result_key_type = key_type
        expr.result_value_type = value_type
        return make_dict(key_type, value_type)

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
        # unwrap_qualifiers (not unwrap_readonly) so a wrapped value-type
        # element still counts as value-type, matching the for-loop predicate
        # in sema/statements.py.
        unwrapped = unwrap_qualifiers(elem_type)
        worth_const_ref = (not unwrapped.is_value_type()
                           or unwrapped.is_expensive_copy())
        if gen.unpack_vars is not None:
            names = [u for u in gen.unpack_vars if u is not None]
        else:
            names = [gen.var]
        # Clear any prior marks in case an outer scope already used these
        # names; the post-body check at the bottom must see only marks from
        # this comp's body.
        for n in names:
            self.ctx.func.mutated_loop_vars.discard(n)
            self.ctx.func.consumed_loop_vars.discard(n)

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
                                                     inner_scope.depth, is_foreach=True))
                    result = self._analyze_comp_body(gen, expr, hint)
            else:
                with self.scopes.loop_var(inner_scope, gen.var, elem_type,
                                          inner_scope.depth, is_foreach=True):
                    result = self._analyze_comp_body(gen, expr, hint)

        if worth_const_ref and not any(n in self.ctx.func.mutated_loop_vars
                                       for n in names):
            gen.const_loop_var = True
        return result

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

    def _analyze_and_strip(self, expr: TpyExpr, hint: TpyType | None = None) -> TpyType:
        """Analyze an expression and strip sema-internal wrappers (Own/Ref).

        Used for container literal element types where the declared type
        should be the user-facing type, not the provenance-tagged expression type.
        """
        if hint is not None:
            self.analyze_expr_with_hint(expr, hint)
        else:
            self.analyze_expr(expr)
        result = self.ctx.get_expr_type(expr)
        assert result is not None
        return result

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
                self.analyze_expr(elem)
                # Use get_expr_type to strip expression-level OwnType/Ref:
                # the tuple's declared element type should be bare T,
                # not the provenance-tagged expression type.
                elem_types.append(self.ctx.get_expr_type(elem))
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
        if isinstance(expr.obj, TpyName) and self.ctx.func.current_ns:
            binding = self.ctx.func.current_ns.lookup(expr.obj.name)
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

        # Unwrap transparent wrappers -- Ref/Own don't affect subscript behavior
        inner_obj_type = unwrap_ref_type(obj_type)
        if isinstance(inner_obj_type, OwnType):
            inner_obj_type = inner_obj_type.wrapped

        # Tuple indexing: t[0], t[-1] -- compile-time constant index only
        actual_for_tuple = unwrap_readonly(inner_obj_type)
        if isinstance(actual_for_tuple, TupleType):
            return self._analyze_tuple_subscript(expr, actual_for_tuple)

        # Slice: obj[start:stop]
        if isinstance(expr.index, TpySlice):
            return self._analyze_slice(expr, inner_obj_type)

        # TypedDict subscript: d["key"] -> field type (compile-time string literal only)
        actual_obj = unwrap_readonly(inner_obj_type)
        if isinstance(actual_obj, NominalType) and actual_obj.is_record:
            record_info = self.ctx.registry.get_record_for_type(actual_obj)
            if record_info and record_info.is_typed_dict:
                if not isinstance(expr.index, TpyStrLiteral):
                    raise self.ctx.error(
                        f"TypedDict '{actual_obj.name}' keys must be string literals", expr.index)
                key = expr.index.value
                type_subst = self.type_ops.build_type_substitution(actual_obj)
                for fld in record_info.fields:
                    if fld.name == key:
                        field_type = fld.type
                        if type_subst:
                            field_type = self.type_ops.substitute_type_params(field_type, type_subst)
                        expr.typed_dict_field = key
                        # total=False: field is Optional[T], unwrap to T with runtime check
                        if isinstance(field_type, OptionalType):
                            expr.typed_dict_optional = True
                            field_type = field_type.inner
                        # Analyze the index expression so its type is recorded
                        self.analyze_expr(expr.index)
                        return make_ref(field_type)
                raise self.ctx.error(
                    f"TypedDict '{actual_obj.name}' has no key '{key}'", expr.index)

        index_type = self.analyze_expr(expr.index)

        # Dict subscript: d[key] -> V (key can be non-integer)
        if isinstance(actual_obj, PendingDictType):
            if not isinstance(actual_obj.key_type, UnknownElementType):
                self.compat.check_type_compatible(
                    index_type, actual_obj.key_type,
                    f"dict key (expected {actual_obj.key_type})",
                    loc=expr.loc,
                    source_expr=expr.index,
                )
            return make_ref(actual_obj.value_type)
        if is_dict(actual_obj):
            k_type = actual_obj.type_args[0]
            v_type = actual_obj.type_args[1]
            self.compat.check_type_compatible(
                index_type, k_type,
                f"dict key (expected {k_type})",
                loc=expr.loc,
                source_expr=expr.index,
            )
            return make_ref(v_type)

        # Slice-typed variable as index: route through __getitem__ overload
        # resolution (same path as literal a:b syntax but with variable index).
        if is_basic_slice_type(index_type) or is_slice_type(index_type):
            stepped = is_slice_type(index_type)
            if stepped:
                expr.is_stepped_slice = True
            is_readonly = isinstance(inner_obj_type, ReadonlyType)
            actual_type = unwrap_readonly(inner_obj_type)
            result = self._find_slice_getitem(actual_type, stepped=stepped, is_readonly=is_readonly)
            if result is not None:
                ret, fi = result
                expr.slice_function_info = fi
                return ret
            raise self.ctx.error(f"Slicing is not supported for {inner_obj_type}", expr)

        if not is_any_int_type(index_type):
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
            return make_ref(elem_type)

        # Protocol types - lookup __getitem__ return type
        if is_protocol_type(actual_type):
            ret = self.narrowing._get_protocol_getitem_type(actual_type)
            if ret is None:
                raise self.ctx.error(f"Protocol {actual_type.name} does not support indexing", expr)
            if is_readonly_obj and not ret.is_value_type():
                ret = ReadonlyType(unwrap_readonly(ret))
            return make_ref(ret)

        # Records with __getitem__ method
        if isinstance(actual_type, NominalType) and actual_type.is_record:
            ret = self.narrowing._get_record_getitem_type(actual_type)
            if ret is None:
                raise self.ctx.error(f"Cannot index type {actual_type}: no __getitem__ method", expr)
            if is_readonly_obj and not ret.is_value_type():
                ret = ReadonlyType(unwrap_readonly(ret))
            return make_ref(ret)

        raise self.ctx.error(f"Cannot index type {obj_type}", expr)

    def _analyze_slice(self, expr: TpySubscript, obj_type: TpyType) -> TpyType:
        """Analyze slice expression: obj[start:stop] or obj[start:stop:step].

        Resolves the __getitem__(basic_slice) or __getitem__(slice) overload
        on the type (built-in or user-defined) and stores the FunctionInfo
        on the expression for codegen.
        """
        sl = expr.index
        assert isinstance(sl, TpySlice)
        stepped = sl.step is not None
        for bound, label in ((sl.lower, "start"), (sl.upper, "stop"), (sl.step, "step")):
            if bound is not None:
                bound_type = self.analyze_expr(bound)
                if not is_any_int_type(bound_type):
                    raise self.ctx.error(
                        f"Slice {label} must be an integer type, got {bound_type}", bound
                    )
        if stepped:
            expr.is_stepped_slice = True

        is_readonly = isinstance(obj_type, ReadonlyType)
        actual_type = unwrap_readonly(obj_type)

        result = self._find_slice_getitem(actual_type, stepped=stepped, is_readonly=is_readonly)
        if result is not None:
            ret, fi = result
            expr.slice_function_info = fi
            return ret

        raise self.ctx.error(f"Slicing is not supported for {obj_type}", expr)

    def _find_slice_getitem(self, actual_type: TpyType, *, stepped: bool = False,
                            is_readonly: bool = False) -> tuple[TpyType, 'FunctionInfo'] | None:
        """Find __getitem__(basic_slice) or __getitem__(slice) overload on any type.

        Works for both built-in types (via qualified_name -> registry) and user records.
        When stepped=False, looks for basic_slice param first, falls back to slice.
        When stepped=True, looks for slice param first, falls back to basic_slice.
        Prefers the const overload when is_readonly=True.
        Returns (return_type, FunctionInfo) or None.
        """
        record = self.ctx.registry.get_record_for_type(actual_type)
        if record is None:
            return None
        getitem_overloads = record.methods.get("__getitem__", [])
        primary = is_slice_type if stepped else is_basic_slice_type
        fallback = is_basic_slice_type if stepped else is_slice_type
        slice_overloads = [
            fi for fi in getitem_overloads
            if len(fi.params) == 1 and primary(fi.params[0].type)
        ]
        if not slice_overloads:
            slice_overloads = [
                fi for fi in getitem_overloads
                if len(fi.params) == 1 and fallback(fi.params[0].type)
            ]
        if not slice_overloads:
            return None
        preferred = [fi for fi in slice_overloads if fi.is_readonly == is_readonly]
        func_info = preferred[0] if preferred else slice_overloads[0]
        ret = func_info.return_type
        type_subst = self.type_ops.build_type_substitution(actual_type)
        if type_subst:
            ret = self.type_ops.substitute_type_params(ret, type_subst)
        # Propagate readonly to Span return types (source is readonly or
        # Span[readonly[T]] -> sliced result should also be readonly)
        if is_span(ret) and not is_readonly_span(ret):
            src_readonly = is_span(actual_type) and is_readonly_span(actual_type)
            if is_readonly or src_readonly:
                ret = make_span(ret.type_args[0], is_readonly=True)
        return ret, func_info

    def _find_slice_setitem(self, actual_type: TpyType, *, stepped: bool = False
                            ) -> tuple[TpyType, 'FunctionInfo'] | None:
        """Find __setitem__(basic_slice, value) or __setitem__(slice, value) overload.

        Similar to _find_slice_getitem but for assignment.
        No fallback from slice to basic_slice (or vice versa) -- unlike
        getitem where a basic_slice can promote to slice for reading, assignment
        semantics differ (stepped requires exact-length match).
        Returns (value_param_type, FunctionInfo) or None.
        """
        record = self.ctx.registry.get_record_for_type(actual_type)
        if record is None:
            return None
        setitem_overloads = record.methods.get("__setitem__", [])
        target = is_slice_type if stepped else is_basic_slice_type
        slice_overloads = [
            fi for fi in setitem_overloads
            if len(fi.params) == 2 and target(fi.params[0].type)
        ]
        if not slice_overloads:
            return None
        func_info = slice_overloads[0]
        value_type = func_info.params[1].type
        type_subst = self.type_ops.build_type_substitution(actual_type)
        if type_subst:
            value_type = self.type_ops.substitute_type_params(value_type, type_subst)
        return value_type, func_info

    _STRINGABLE = NominalType("Stringable", is_protocol=True)
    _REPRESENTABLE = NominalType("Representable", is_protocol=True)

    @staticmethod
    def _is_formattable(t: TpyType) -> bool:
        """True for types that f-string can format directly (no __str__/__repr__ needed)."""
        return (is_numeric_type(t) or is_char_type(t) or is_any_str_type(t)
                or isinstance(t, (IntLiteralType, FloatLiteralType))
                or is_enum_type(t))

    def _is_fstring_renderable(self, t: TpyType, repr_only: bool = False) -> bool:
        """True if t can appear in an f-string slot (and is __repr__-able when
        repr_only). Recurses into UnionType members (codegen dispatches via
        std::visit to the runtime variant __str__/__repr__ overloads)."""
        if isinstance(t, UnionType):
            return all(self._is_fstring_renderable(m, repr_only) for m in t.members)
        if repr_only:
            if self.protocols.type_conforms_to_protocol(t, self._REPRESENTABLE):
                return True
            # Built-in primitives have runtime __repr__ overloads but no
            # method-level Representable conformance; treat formattable
            # types as repr-able too.
            return self._is_formattable(t)
        if self._is_formattable(t):
            return True
        if self.protocols.type_conforms_to_protocol(t, self._STRINGABLE):
            return True
        return self.protocols.type_conforms_to_protocol(t, self._REPRESENTABLE)

    def _analyze_fstring(self, expr: TpyFString, *, for_fstr: bool = False) -> TpyType:
        """Analyze f-string parts and return STR (owned string).

        Args:
            for_fstr: If True, only analyze expression types without validating
                formattability. Used for FStr parameters where the call macro
                handles per-type dispatch (types don't need __str__/__repr__).
        """
        for part in expr.parts:
            if isinstance(part, TpyFStringValue):
                part_type = self.analyze_expr(part.expr)
                if for_fstr:
                    continue
                resolved = unwrap_readonly(part_type)
                if isinstance(resolved, OwnType):
                    resolved = resolved.wrapped
                conv = part.conversion

                if container_to_str_template(resolved) is not None:
                    pass  # containers have runtime to_str
                elif isinstance(resolved, AnyType):
                    pass  # Any dispatches via the per-type str/repr ops slot
                elif conv == FSTRING_CONV_REPR:
                    if not self._is_fstring_renderable(resolved, repr_only=True):
                        raise self.ctx.error(
                            f"Type {part_type} cannot use !r conversion (no __repr__ method)",
                            part.expr,
                        )
                elif conv == FSTRING_CONV_STR:
                    if not self._is_fstring_renderable(resolved):
                        raise self.ctx.error(
                            f"Type {part_type} cannot use !s conversion"
                            " (no __str__ or __repr__ method)",
                            part.expr,
                        )
                elif not self._is_fstring_renderable(resolved):
                    raise self.ctx.error(
                        f"Type {part_type} cannot be used in f-string"
                        " (no __str__ or __repr__ method)",
                        part.expr,
                    )
                if part.format_spec is not None and (is_big_int_type(resolved) or isinstance(resolved, IntLiteralType)):
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

    def _analyze_lambda_with_fn_hint(self, expr: TpyLambda, fn_type: CallableType) -> CallableType:
        """Analyze a lambda with a Fn/Callable type hint providing parameter types."""
        type_name = "Fn" if is_fn_type(fn_type) else "Callable"
        if len(expr.param_names) != len(fn_type.param_types):
            raise self.ctx.error(
                f"Lambda has {len(expr.param_names)} parameter(s) but "
                f"{type_name} type expects {len(fn_type.param_types)}",
                expr
            )

        expr.inferred_param_types = list(fn_type.param_types)

        # Save outer scope locals for capture filtering
        outer_locals = set(self.ctx.func.definitely_assigned)

        with self.scopes.lambda_scope() as scope:
            for pname, ptype in zip(expr.param_names, fn_type.param_types):
                scope.define(pname, ptype)
                if self.ctx.func.current_ns:
                    self.ctx.func.current_ns.bind_variable(pname, ptype)
                self.ctx.func.definitely_assigned.add(pname)

            body_type = self.analyze_expr(expr.body)

        # Detect captures: names in body that are local variables from the outer scope
        # (not lambda params, not global functions, not builtins)
        param_set = set(expr.param_names)
        free_names = collect_name_refs(expr.body)
        captured = sorted((free_names - param_set) & outer_locals)
        expr.captured_names = captured
        # Callable context: captures must be by value (std::function can escape).
        # Fn (template) stays inline; captures by reference are safe.
        if isinstance(fn_type, CallableType) and not fn_type.is_template:
            expr.captures_by_value = True
        # Send/Sync frame fact: classify the capture list so conversion
        # sites (Send[Callable[...]] slots) can consult the concrete frame.
        by_ref = not expr.captures_by_value
        expr.frame_type = build_closure_frame([
            (name, self._lookup_capture_type(name), by_ref)
            for name in captured
        ])

        # Check return type compatibility (allow implicit coercions like int literal -> Int32)
        if isinstance(fn_type.return_type, TypeParamRef):
            # Hint has unresolved type param (e.g. from generic builtin map[T,U]):
            # use the body's inferred type and return a concrete CallableType.
            # Resolve IntLiteralType so overload resolution sees a concrete int type.
            if isinstance(body_type, IntLiteralType):
                body_type = self.ctx.default_int_for_literal(body_type)
            expr.inferred_return_type = body_type
            self.compat.check_view_return_dangle(expr.body, body_type, expr.loc)
            concrete_params = tuple(fn_type.param_types)
            if fn_type.is_template:
                return make_fn_type(concrete_params, body_type)
            return CallableType(concrete_params, body_type)
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
        self.compat.check_view_return_dangle(expr.body, fn_type.return_type, expr.loc)

        expr.inferred_return_type = fn_type.return_type
        return fn_type

    def _lookup_capture_type(self, name: str) -> 'TpyType | None':
        """Declared type of a lambda-captured outer local (None when the
        binding is not a plain variable -- classified conservatively)."""
        ns = self.ctx.func.current_ns
        binding = ns.lookup(name) if ns else None
        if binding is not None and binding.kind == BindingKind.VARIABLE:
            return binding.type
        return None

    # --- Function references ---

    def _try_resolve_function_ref(
        self, expr: TpyName, hint: CallableType,
    ) -> CallableType | None:
        """Try to resolve a name as a function reference matching an Fn/Callable hint.

        Returns a concrete Fn/Callable type if a matching function is found,
        None to fall through to normal name analysis.
        """
        # Look up in namespace -- variables shadow functions
        if self.ctx.func.current_ns:
            binding = self.ctx.func.current_ns.lookup(expr.name)
            if binding:
                if binding.kind == BindingKind.VARIABLE:
                    return None  # local variable shadows any function
                if binding.kind == BindingKind.FUNCTION and binding.func_infos:
                    matched_data = self._match_function_to_hint_data(
                        binding.func_infos, hint, expr.name, expr)
                    if matched_data is not None:
                        matched, type_args = matched_data
                        if type_args is not None:
                            expr.function_ref_type_args = type_args
                        expr.is_function_ref = True
                        expr.function_ref_info = matched
                        # Escape tracking: passing nested def to Callable (type-erased)
                        # marks it as escaping. Fn (template) stays inline -- no escape.
                        if (isinstance(hint, CallableType) and not hint.is_template
                                and expr.name in self.ctx.func.nested_def_names):
                            self.ctx.func.nested_def_escapes.add(expr.name)
                        return self._concrete_fn_type(matched, expr, hint)

        # Check registry (covers imported functions not yet in namespace)
        func_infos = self.ctx.registry.get_function(expr.name)
        if func_infos:
            matched_data = self._match_function_to_hint_data(
                func_infos, hint, expr.name, expr)
            if matched_data is not None:
                matched, type_args = matched_data
                if type_args is not None:
                    expr.function_ref_type_args = type_args
                expr.is_function_ref = True
                expr.function_ref_info = matched
                return self._concrete_fn_type(matched, expr, hint)

        return None

    def _concrete_fn_type(
        self, fi: FunctionInfo, expr: TpyName, hint: CallableType,
    ) -> CallableType:
        """Build a concrete Fn/Callable type from a matched function's signature.

        When the hint has TypeParamRef (e.g. from a generic builtin like map[T,U]),
        returns the concrete type from the function's actual signature so that
        overload resolution can infer the outer type params.
        """
        if not contains_type_param(hint):
            return hint
        return self.build_concrete_callable(fi, expr.function_ref_type_args, hint)

    def build_concrete_callable(
        self, fi: FunctionInfo,
        type_args: tuple[TpyType, ...] | None,
        hint: CallableType,
    ) -> CallableType:
        """Concrete Fn/Callable from `fi`'s actual signature, optionally
        substituted with `type_args`.

        Strips Ref from param types and Own from return type -- the Fn
        type represents the logical callable contract. Ref on return
        type IS preserved so type inference can track reference
        semantics through combinators (e.g. map(identity, pts) infers
        U=Ref[Point] -> val_or_ref<Point>). Shape mirrors `hint` -- Fn
        if template, Callable otherwise.
        """
        param_types = tuple(unwrap_ref_type(ptype) for _, ptype in fi.params)
        return_type = unwrap_own(fi.return_type)
        if fi.is_generic() and type_args:
            subst = dict(zip(fi.type_params, type_args))
            param_types = tuple(
                self.type_ops.substitute_type_params(p, subst) for p in param_types
            )
            return_type = self.type_ops.substitute_type_params(return_type, subst)
        if hint.is_template:
            return make_fn_type(param_types, return_type)
        return CallableType(param_types, return_type)

    def _match_function_to_hint_data(
        self, func_infos: list[FunctionInfo], hint: CallableType,
        name: str, err_node: TpyName,
    ) -> tuple[FunctionInfo, tuple[TpyType, ...] | None] | None:
        """Find a function overload matching the Fn/Callable hint signature.

        Pure data lookup: returns ``(matched_fi, inferred_type_args)`` on a
        unique match (``type_args`` is ``None`` for non-generic matches),
        ``None`` when no overload matches (callers may treat the name as a
        variable instead). Raises ``SemanticError`` on ambiguity or
        generic-rejection -- ``err_node`` provides source location and is
        not otherwise mutated, so this matcher is safe to use during
        speculative overload probing.

        Callers that want the AST mutated (``is_function_ref``,
        ``function_ref_info``, ``function_ref_type_args``) commit those
        themselves after a winning candidate is chosen.
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
            return candidates[0]
        if len(candidates) > 1:
            raise self.ctx.error(
                f"Ambiguous function reference: multiple overloads of '{name}' "
                f"match {hint}", err_node)
        # No match -- emit generic rejection diagnostic if we have one
        if generic_rejection is not None:
            raise self.ctx.error(generic_rejection, err_node)
        # Return None to fall through (might be a variable, not a function)
        return None

    def _infer_generic_ref_type_args(
        self, fi: FunctionInfo, hint: CallableType,
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
