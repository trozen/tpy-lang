"""
TurboPython Expression Analysis

Core expression analysis including literals, names, operators, field access, and subscripts.
"""

from __future__ import annotations
from contextlib import AbstractContextManager, ExitStack, nullcontext
from dataclasses import replace as dc_replace
from typing import Callable, Iterable, Literal, Sequence, TYPE_CHECKING

from ..typesys import peel_value_readonly
from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, RecordInfo, disambiguated_pair,
    NominalType, PtrType, OwnType, make_array, make_dict, make_set, make_span, make_list, span_as_const, span_as_mutable, PendingListType, ListRepeatType, GenExprType, TupleType, unify_literal_types,
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
    unwrap_own, coro_struct_owner, is_readonly_span, collapse_tuple_own_elements, global_binds_by_reference, owned_tuple_storage_type,
    ConcreteCoroType, make_concrete_gen, is_dyn_protocol,
    RecursiveAliasInstanceType, recursive_union_alternatives)
from ..parse.nodes import GENEXPR_FUNC_PREFIX, op_spelling
from ..parse import (
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBytesLiteral,
    TpyFStringValue, TpyFString, FSTRING_CONV_REPR, FSTRING_CONV_STR,
    TpyBoolLiteral,
    TpyNoneLiteral, TpyName, TpyBinOp, TpyChainedCompare, TpyUnaryOp, TpyTypeParamConstruct,
    TpyCall, TpyMethodCall, TpyFieldAccess, TpyFunction,
    is_stable_address_lvalue, is_property_getter_read, become_method_call,
    walk_body_stmts,
    TpyArrayLiteral, TpyTupleLiteral, TpyDictLiteral, TpySetLiteral, TpyListRepeat,
    TpyListComprehension, TpyDictComprehension, TpySetComprehension, TpyGeneratorExpression, TpyComprehensionGenerator,
    TpySlice, TpySubscript, TpyCoerce,
    TpyIfExpr, TpyNamedExpr, TpyAwait,
    TpyLambda, TpyStarUnpack,
    TpyStmt, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyForEach, TpyWith,
    TpyNestedDef, TpyIf, TpyYield, TpyTry, TpyAugAssign, TpyNonlocal,
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
    type_def_of,
)
from ..namespace import BindingKind, NameBinding
from .frame_traits import build_closure_frame
from ..coercions import CoercionContext, resolve_coercion
from ..prescan import (
    _expr_to_narrowing_key, bound_names_of, storage_spelling, walrus_names_of)
from ..diagnostics import SemanticError, OPTIONAL_NONE_ACCESS_WARNING
from .. import qnames
from .context import PENDING_CONTAINER_TYPES, _root_name_of_expr, _storage_root, is_body_like_scope, register_binding_borrow, ephemeral_borrow_root, record_stmt_borrow_binding, contains_pending_leaf, note_owned_local, holds_generator_object, frame_binding_fact, record_frame_binding_roots, call_param_args
from ..value_category import (is_rvalue_source, async_result_aliases,
                             return_type_is_cpp_ref, peel_value_wrappers,
                             lent_operands)
from .alias_rebind import bind_kind_of
from .compatibility import TupleSink
from .narrowing import NarrowingTracker, deref_view_narrowed, truthy_operands
from .numeric_lattice import widen_numeric_types
from .type_join import (InferredJoin, JoinOutcome, declared_float_slot,
                        peel_value,
                        find_int_float_mix, flipped,
                        float_left_open, join_inferred_value_types,
                        literal_mix_message,
                        operand_spelling, python_type_name,
                        select_mix_message)
from .list_literals import IterableHelper
from .slot_hint import (SlotHint, callable_return, element,
                        optional_inner, type_arg)
from .local_deduction import collect_pending_source_types, mark_pending_list_mutated
from .operators import DUNDER_CPP_TEMPLATES, _substitute_type_params
from .bound_check import raise_if_class_param_bound_violated
from .overloads import OverloadAmbiguityError, resolve_overload
from .type_ops import frame_yield_may_borrow
from .scope_tracker import lend_roots
from .receiver_calls import (call_mutates_receiver, check_implicit_readonly_receiver,
                             credit_implicit_receiver_call)
from .iter_loans import (_record_iter_receiver_mutation, iterated_storage,
                         register_iteration_loans)

if TYPE_CHECKING:
    from ..parse.nodes import SourceLocation
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .operators import OperatorResolver
    from .protocols import ProtocolChecker
    from .compatibility import TypeCompatibility
    from .calls import CallAnalyzer
    from .methods import MethodAnalyzer
    from .scope_tracker import ScopeTracker
    from ..type_def_registry import TypeDef

from tpyc import modules as builtin_modules


def _collect_body_name_refs(stmts: list[TpyStmt],
                           into_lambdas: bool = False) -> set[str]:
    """Collect all name references from a list of statements.

    Walks all expressions in statements to find free variable references.
    Does NOT recurse into nested function definitions (separate scope);
    `into_lambdas` opts into the lambda bodies, which are withheld for the
    same reason -- see `collect_name_refs`.
    """
    names: set[str] = set()

    def on_expr(expr: TpyExpr) -> None:
        names.update(collect_name_refs(expr, into_lambdas=into_lambdas))

    walk_body_stmts(stmts, on_expr, lambda s: None)
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

    walk_body_stmts(stmts, lambda e: None, on_stmt)
    return defs


def _has_pending_literal_below(typ: TpyType, first_own: int) -> bool:
    """`typ` is, or nests, a pending container literal created before literal
    id `first_own`."""
    if isinstance(typ, (PendingListType, PendingDictType, PendingSetType)):
        return typ.literal_id < first_own
    return any(_has_pending_literal_below(t, first_own) for t in typ.inner_types())


def _names_rebound_by(stmt: TpyStmt) -> set[str]:
    """The names this ONE statement can leave bound to a different value than
    before: the whole-name binders and walruses prescan enumerates, an
    `except ... as` target, and every `nonlocal` a nested def declares (it may
    run whenever the def is called). An augmented assignment is not one: it
    updates the value the name already has, which keeps its narrowing."""
    names = set() if isinstance(stmt, TpyAugAssign) else set(bound_names_of(stmt))
    names |= walrus_names_of(stmt)
    if isinstance(stmt, TpyTry):
        names.update(h.binding for h in stmt.handlers if h.binding is not None)
    if isinstance(stmt, TpyNestedDef):
        def on_inner(inner: TpyStmt) -> None:
            if isinstance(inner, TpyNonlocal):
                names.update(inner.names)
        walk_body_stmts(stmt.func.body, lambda e: None, on_inner)
    return names


def _rebound_in(stmts: list[TpyStmt]) -> set[str]:
    """Every name some statement of a body can rebind."""
    names: set[str] = set()
    walk_body_stmts(stmts, lambda e: None,
                    lambda stmt: names.update(_names_rebound_by(stmt)))
    return names


def _nested_def_free_names(func: TpyFunction) -> set[str]:
    """Names a nested def's body reads from the scope the `def` is written in.

    A binding of its own -- a parameter, an assignment, a loop variable --
    shadows the enclosing name and is not such a read. A `nonlocal` target is
    not counted here either: the caller has the authoritative set and adds it
    back, because it is a write THROUGH to the enclosing binding.
    """
    return (_collect_body_name_refs(func.body)
            - {p for p, _ in func.params}
            - _collect_body_local_defs(func.body))


def _union_like_members(ut: TpyType) -> 'tuple[TpyType, ...]':
    """The variant alternatives of a recursive-union wrapper -- the non-generic
    UnionType's members or a generic instance's alternatives."""
    if isinstance(ut, UnionType):
        return ut.members
    return recursive_union_alternatives(ut) or ()


def generic_constructor_factory(expr: TpyExpr) -> 'TypeDef | None':
    """The generic type factory an argument-less constructor call such as
    `list()`, `dict()` or `set()` names, whose type arguments only its
    context can give; None for any other expression."""
    if not (isinstance(expr, TpyCall) and isinstance(expr.func, TpyName)
            and not expr.args and expr.call_type is None):
        return None
    td = find_factory_by_simple_name(expr.func_name)
    return td if td is not None and td.param_kinds else None


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


# Largest fixed comprehension that still resolves to a stack Array. Above
# this the per-element pack expansion in tpy::array_from_index (and the stack
# footprint) outweigh the heap save, so resolution falls back to list.
_COMP_ARRAY_MAX_SIZE = 1024


def _readonly_result(ret: TpyType) -> TpyType:
    """A reference result read off a readonly receiver: `readonly[T]` under
    the caller's `Ref`, the shape a builtin container element takes -- a
    `__getitem__` declared to return a reference carries its own `Ref`,
    which would otherwise end up INSIDE the readonly."""
    return ReadonlyType(unwrap_readonly(unwrap_ref_type(ret)))


def _is_slice_getitem(fi: 'FunctionInfo') -> bool:
    return (len(fi.params) == 1
            and (is_basic_slice_type(fi.params[0].type)
                 or is_slice_type(fi.params[0].type)))


def _protocol_getitem_is_readonly(protocol: NominalType) -> bool:
    info = protocol_info_of(protocol)
    if info is None:
        return True
    return all(m.is_readonly for m in info.methods if m.name == "__getitem__")


def _expr_has_error_return_call(expr: TpyExpr) -> bool:
    """True when the (analyzed) expression contains a call resolved to an
    @error_return function. Mirrors the condition codegen's
    _maybe_error_return_unwrap dispatches on (resolved_function_info with an
    error_return_type), so consumers can tell whether emitting the expression
    will produce a function-targeting unwrap."""
    fi = getattr(expr, 'resolved_function_info', None)
    if fi is not None and fi.error_return_type:
        return True
    # dyn_getattr_call is a call outside children() but needs no traversal
    # ONLY because registration bans @error_return on dyn-attr dunders;
    # revisit here if that ban is ever relaxed.
    for child in (expr.children() if hasattr(expr, "children") else ()):
        if _expr_has_error_return_call(child):
            return True
    return False


def _temp_root_spelling(root: TpyExpr) -> str | None:
    """How a diagnostic names the call that creates a temporary: its callee
    with the arguments elided, or None when the callee has no short name."""
    if isinstance(root, TpyCall) and isinstance(root.func, TpyName):
        return f"{root.func.name}(...)"
    if isinstance(root, TpyMethodCall):
        obj = storage_spelling(root.obj)
        return f"{obj}.{root.method}(...)" if obj is not None else None
    return None


class _LambdaResultMismatch(SemanticError):
    """A lambda body whose type does not fit the slot's return type; a
    class-factory lambda rewords it in terms of the class the user wrote."""
    def __init__(self, message: str, loc: 'SourceLocation | None',
                 body_type: TpyType) -> None:
        super().__init__(message, loc)
        self.body_type = body_type


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

    def analyze_call_arg(
        self, arg: TpyExpr,
        hint: 'SlotHint | TpyType | None' = None,
    ) -> TpyType:
        """Analyze one call argument, transparently handling `*xs` unpack.

        Every call-argument pre-analyzer (generic inference, overload
        probing, vararg packing -- in both `calls.py` and `methods.py`)
        must route args through here rather than `analyze_expr` directly:
        a `TpyStarUnpack` carries no expression-type semantics of its own,
        so feeding it to the structural analyzer hits the catch-all
        "Unknown expression type" error. For a `*container` arg this
        returns the container's element type (the value an individual
        positional arg would have contributed); for everything else it
        is `analyze_expr[_with_hint]`. No `hint` (None) keeps the enclosing
        call's hint in force.
        """
        if isinstance(arg, TpyStarUnpack):
            return self._unpack_star_element_type(arg, SlotHint.of(hint))
        return self.analyze_expr_with_hint(arg, hint)

    def _unpack_star_element_type(
        self, node: TpyStarUnpack, elem_hint: 'SlotHint | None',
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
        inner_hint = elem_hint.map(make_list) if elem_hint is not None else None
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

    def _concrete_generator_call(self, init: TpyExpr,
                                 iterator: TpyType) -> TpyType:
        """The type of a direct generator call: the concrete frame it builds
        (a non-copyable reference type, bound and aliased like a record),
        or `iterator` unchanged where the frame struct is not nameable from
        the call site -- a callee with static-protocol / Fn params (its
        struct takes call-site-deduced template args) or an overloaded
        generator."""
        fi = getattr(init, "resolved_function_info", None)
        it = unwrap_readonly(iterator)
        if (fi is None or not fi.is_generator
                or not isinstance(it, NominalType)
                or it.qualified_name() != qnames.ITERATOR):
            return iterator
        self._reject_frame_held_adapter(init, fi)
        for p in fi.params:
            pt = unwrap_readonly(unwrap_ref_type(p.type))
            if (is_protocol_type(pt) and not is_dyn_protocol(pt)) or is_fn_type(pt):
                return iterator
        owner = None
        if isinstance(init, TpyMethodCall):
            recv_type = self.ctx.get_expr_type(init.obj)
            recv_inner = unwrap_own(unwrap_ref_type(recv_type)) if recv_type else None
            if not isinstance(recv_inner, NominalType):
                return iterator
            owner = coro_struct_owner(
                fi.owning_type_qname, recv_inner,
                self.ctx.registry.get_record_for_type(recv_inner))
            # Inside a generic class's own body the owner's args are its
            # open parameters: the struct is spelled by the class template's
            # member context there, not from a call site.
            if any(contains_type_param(a) for a in owner.type_args
                   if isinstance(a, TpyType)):
                return iterator
        module_qual = None
        if (owner is None and fi.originating_module is not None
                and fi.originating_module != self.ctx.module_name):
            module_qual = fi.originating_module
        targs = getattr(init, "inferred_type_args", None)
        return make_concrete_gen(
            it, fi.name, owner=owner,
            inferred_type_args=tuple(targs) if targs else None,
            module_qual=module_qual)

    def _reject_frame_held_adapter(self, call: TpyExpr,
                                   fi: 'FunctionInfo') -> None:
        """Inside a generator or coroutine body, a generator given a value
        for a @dynamic protocol parameter keeps the protocol view of it,
        which lives only until the next suspension, while the generator may
        live in the frame across it."""
        func = self.ctx.func.current_function
        if not (getattr(func, "is_generator", False)
                or getattr(func, "is_async", False)):
            return
        for p, arg in call_param_args(call):
            pt = unwrap_readonly(unwrap_ref_type(p.type))
            if not is_dyn_protocol(pt):
                continue
            while isinstance(arg, TpyCoerce):
                arg = arg.expr
            at = self.ctx.get_expr_type(arg)
            if at is not None and unwrap_readonly(unwrap_ref_type(
                    unwrap_own(at))) == pt:
                continue
            raise self.ctx.error(
                f"cannot pass this value to @dynamic protocol parameter "
                f"'{p.name}' of generator '{fi.name}' inside a generator or "
                f"async function: the '{pt}' view of it lasts only until the "
                f"next yield or await, and the generator may outlive that; "
                f"give '{fi.name}' a parameter of the concrete type, or bind "
                f"the value to a '{pt}'-typed parameter of this function",
                call)

    def analyze_expr(self, expr: TpyExpr) -> TpyType:
        """Analyze an expression and return its type."""
        own = self.ctx.slot_hint_at(expr)
        if own is not None and own.is_fill and self._fill_types(expr):
            # What the literal or empty constructor leaves open -- its
            # empty element types, its float literals' width -- is filled
            # from the overload's declared parameter type, which converts
            # none of its ints: the same typing an inferred local's type
            # gives.
            return self._analyze_expr_under_hint(expr, own.as_local())
        if is_property_getter_read(expr):
            # A property read BECAME its getter call at the end of its own
            # analysis, and bodies re-analyse expressions (a chained
            # comparison's middle operand, the receiver of a write target, a
            # lambda body under an overload dry-run). The getter is pruned
            # from the record's method table, so re-running METHOD resolution
            # would reject a read that already resolved.
            # It is re-RESOLVED rather than read back: the become is
            # permanent while a `trial_scope` rolls the type table back, so
            # the recorded type is not there on every second pass. The probe
            # node carries the same receiver and name the first pass saw, so
            # the read resolves through exactly the path that typed it.
            probe = TpyFieldAccess(obj=expr.obj, field=expr.method,
                                   loc=expr.loc)
            typ = self._analyze_field_access(probe)
            # STAMP it: a trial scope rolls the table back, so returning the
            # type without recording it leaves the read untyped for every
            # consumer downstream of the winning candidate's re-analysis --
            # which is what cost the lambda-under-dispatch position. The
            # probe is a throwaway; this node is the one that lowers.
            self.ctx.set_expr_type(expr, typ)
            return typ
        if isinstance(expr, TpyIntLiteral):
            typ = IntLiteralType(expr.value)
        elif isinstance(expr, TpyFloatLiteral):
            typ = FloatLiteralType(expr.value)
        elif isinstance(expr, TpyStrLiteral):
            # String literals are always str type (including single-char)
            # char type is only used when explicitly annotated or from string indexing
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
            typ = self._concrete_generator_call(
                expr, self.calls.analyze_call(expr))
            self.narrowing.invalidate_field_facts_for_call(expr)
            self.narrowing.invalidate_closure_written_facts()
        elif isinstance(expr, TpyMethodCall):
            typ = self._concrete_generator_call(
                expr, self.methods.analyze_method_call(expr))
            self.narrowing.invalidate_field_facts_for_method_call(expr)
            self.narrowing.invalidate_closure_written_facts()
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
            # The operand above was analyzed pre-suspension; whatever the
            # enclosing statement does with the result runs post-resume,
            # when the caller may have mutated shared storage.
            self.narrowing.invalidate_suspension_facts()
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

    def analyze_condition(self, expr: TpyExpr) -> TpyType:
        """Analyze a truth-test position: an `if` / `while` / `assert` head,
        a comprehension filter, a ternary test, a `not` operand, the argument
        of a constructor that only tests it (`bool(...)`)."""
        self.mark_truth_test(expr)
        return self.analyze_expr(expr)

    def mark_truth_test(self, expr: TpyExpr) -> None:
        """Record that `expr` is only tested for truth. The `and` / `or` /
        ternary nodes the test distributes over are themselves truth tests,
        so their operands need no one value type. Must precede the first
        analysis of `expr`, which caches its type."""
        selects: list[TpyExpr] = []
        truthy_operands(expr, selects)
        self.ctx.truth_test_selects.update(selects)

    def analyze_expr_with_hint(self, expr: TpyExpr,
                               hint: SlotHint | TpyType | None) -> TpyType:
        """Analyze an expression with an optional type hint for inference.

        The type hint allows constructs like list() to infer their type parameters
        from context (e.g., function parameter type). A bare type is a DECLARED
        hint; a `SlotHint` also says which positions an inferred local's type
        decides, where the hint types what the value leaves open but converts
        no int into a float. With no hint the enclosing one stays in force.
        """
        slot = SlotHint.of(hint)
        if slot is None:
            return self.analyze_expr(expr)
        return self._analyze_expr_under_hint(expr, slot)

    @staticmethod
    def _fill_types(expr: TpyExpr) -> bool:
        """The argument shapes a fill-only hint (`SlotHint.fill`) types as
        a whole; a generic call's or a record construction's type
        parameters are filled by their inference. A lambda is not one:
        typed from one candidate before the winner is chosen, it would keep
        that typing if another won (the reason `_probe_arg_hint` drops a
        Callable hint), so it gets no fill, as a declared local's probe
        gives it no hint."""
        return (isinstance(expr, (TpyArrayLiteral, TpyDictLiteral,
                                  TpySetLiteral, TpyTupleLiteral,
                                  TpyListComprehension, TpyDictComprehension,
                                  TpySetComprehension))
                or generic_constructor_factory(expr) is not None)

    def _forward_fill(self, select: TpyExpr, operand: TpyExpr,
                      ) -> AbstractContextManager[None]:
        """A ternary's or an and/or's value is one of its operands, so a
        fill-only hint for the select is one for each operand."""
        own = self.ctx.slot_hint_at(select)
        if own is None or not own.is_fill:
            return nullcontext()
        return self.ctx.slot_hint_scope(own.retarget(operand))

    def _hint_converts_int(self, value: TpyType,
                           hint: SlotHint | None) -> bool:
        """Whether binding `value` at `hint` would turn an int in it into a
        float at a position an inferred local decides: there the int stays
        an int, for the join the value meets next to refuse. Every site
        that would convert under a hint asks here."""
        view = hint.inferred_view() if hint is not None else None
        return view is not None and self._view_converts(view, value)

    def _view_converts(self, view: TpyType, value: TpyType) -> bool:
        if self.compat.deduction.int_float_mix(view, value) is not None:
            return True
        # A callable's return is the value it hands on: a lambda returning
        # an int at an inferred float return keeps its int return.
        view, value = peel_value(view)[0], peel_value(value)[0]
        return (isinstance(view, CallableType)
                and isinstance(value, CallableType)
                and self._view_converts(view.return_type, value.return_type))

    def _converting_element(self, types: Iterable[TpyType],
                            hint: SlotHint | None) -> TpyType | None:
        """The first of a literal's or comprehension's element types the
        hint would convert (`_hint_converts_int`): the container then keeps
        its own element types, for the join it meets next to refuse."""
        return next((t for t in types if self._hint_converts_int(t, hint)),
                    None)

    @staticmethod
    def _hint_gave_float(mix: InferredJoin, peers: Sequence[TpyExpr],
                         hint: SlotHint | None) -> bool:
        """Whether a hinted literal's peers mix an int only with a float the
        hint gave an element the source left empty (`[[], [2]]` over a
        `list[list[float]]` local). The source then mixes nothing: the
        literal keeps its int side, and the join with the hint's source --
        the rebound local's own type -- refuses it, naming the local."""
        return hint is not None and float_left_open(
            mix, peers, lambda e: generic_constructor_factory(e) is not None)

    def _mark_list_annotated(self, typ: TpyType, list_hint: SlotHint,
                             coerced_elem: TpyType | None) -> None:
        """Record the list type a pending list literal or comprehension was
        analyzed against as its annotation, which its deferred resolution
        then follows -- unless that would turn the ints it holds into the
        hint's floats (`_hint_converts_int`)."""
        if (not isinstance(typ, PendingListType)
                or self._hint_converts_int(typ, list_hint)):
            return
        info = self.ctx.list_literals.get(typ.literal_id)
        if info is None:
            return
        info.has_explicit_annotation = True
        info.explicit_type = list_hint.type
        if coerced_elem is not None and info.coerced_element_type is None:
            info.coerced_element_type = coerced_elem

    @staticmethod
    def _bare_hint(hint: SlotHint) -> SlotHint:
        """`hint` without its readonly / Own wrappers."""
        return hint.map(lambda t: unwrap_own(unwrap_readonly(t)))

    def _analyze_expr_under_hint(self, expr: TpyExpr,
                                 slot: SlotHint) -> TpyType:
        slot = slot.map(unwrap_ref_type)
        type_hint = slot.type

        # Lambda with Fn/Callable type hint: infer param types from the hint.
        # Transparent wrappers don't change the callable's shape, so peel them
        # before the is_callable_type check: Send/Sync markers (which only
        # constrain the conversion, checked by _check_compat), Own at consuming
        # slots, and a single Optional (a `Callable[...] | None` param
        # exposes its inner callable to a lambda/function-ref arg, which coerces
        # back into the optional slot afterwards). `Send[Callable] | None` is NOT
        # handled: its marker sits inside the Optional, and narrowing + the call
        # path would also need to peel it -- see TODO.
        def callable_part(t: TpyType) -> TpyType:
            t = unwrap_own(unwrap_send_sync(t))
            if isinstance(t, OptionalType):
                t = t.inner
            return unwrap_own(t)
        lambda_slot = slot.map(callable_part)
        lambda_hint = lambda_slot.type
        if isinstance(expr, TpyLambda) and is_callable_type(lambda_hint):
            typ = self._analyze_lambda_with_fn_hint(expr, lambda_slot)
            self.ctx.set_expr_type(expr, typ)
            return typ

        # Named function reference with Fn/Callable hint: resolve as function value.
        if isinstance(expr, TpyName) and is_callable_type(lambda_hint):
            result = self._try_resolve_function_ref(expr, lambda_hint)
            ref = expr.function_ref_info if result is not None else None
            if (isinstance(result, CallableType) and ref is not None
                    and self._hint_converts_int(
                        ref.return_type,
                        lambda_slot.map(callable_return))):
                # As for a lambda: an inferred hint's float return does not
                # convert the function's int result.
                own_return = unwrap_own(ref.return_type)
                result = (make_fn_type(result.param_types, own_return)
                          if result.is_template
                          else CallableType(result.param_types, own_return))
            if result is None:
                result = self._try_class_factory(expr, lambda_slot)
            if result is not None:
                self.ctx.set_expr_type(expr, result)
                return result

        # An and/or at a declared slot: its operands are analysed against
        # the declared type first, as a ternary's arms are.
        if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
            typ = self._analyze_binop(expr, declared_slot=slot)
            self.ctx.set_expr_type(expr, typ)
            return typ

        # Ternary expression: propagate hint to both branches
        if isinstance(expr, TpyIfExpr):
            typ = self._analyze_if_expr(expr, type_hint=slot)
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
        tuple_slot = slot.map(unwrap_own)
        tuple_hint = tuple_slot.type
        if isinstance(expr, TpyTupleLiteral) and isinstance(tuple_hint, TupleType):
            if len(expr.elements) == len(tuple_hint.element_types):
                hints = [tuple_slot.map(element(k))
                         for k in range(len(tuple_hint.element_types))]
                typ = self._analyze_tuple_literal(expr, element_hints=hints)
                self.ctx.set_expr_type(expr, typ)
                return typ

        ctor_td = generic_constructor_factory(expr)
        is_generic_constructor = ctor_td is not None

        # Check for empty list literal []
        is_empty_literal = isinstance(expr, TpyArrayLiteral) and not expr.elements

        if is_generic_constructor or is_empty_literal:
            # Unwrap ReadonlyType/OwnType so hints like Own[list[T]] work
            inner_hint = unwrap_readonly(type_hint)
            if isinstance(inner_hint, OwnType):
                inner_hint = inner_hint.wrapped
            # Check if type_hint matches the constructor's generic type
            hint_matches = False
            # An empty `[]` can also target a recursive-union hint through its
            # list member (e.g. JsonValue's list[JsonValue]), not just a direct
            # list[T] hint.
            union_list_member = None
            if ctor_td is not None:
                hint_matches = inner_hint.qualified_name() == ctor_td.qname
            elif is_list(inner_hint):
                # Empty literal [] can match list[T] hint
                hint_matches = True
            elif inner_hint.needs_wrapper():
                union_list_member = _find_list_member(inner_hint)
                hint_matches = union_list_member is not None

            if hint_matches:
                list_hint = inner_hint if is_list(inner_hint) else union_list_member
                if list_hint is not None:
                    # list[T]: Use PendingListType for potential Array optimization
                    elem_type = list_hint.get_element_type()
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
                            explicit_type=list_hint,
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
        # (e.g. return [x, y] with -> Own[list[T]], or x: list[int32|None] = [1, None])
        if isinstance(expr, TpyArrayLiteral) and expr.elements:
            inner = self._bare_hint(slot)
            inner_hint = inner.type
            if is_array(inner_hint) or is_list(inner_hint):
                result = self._analyze_array_literal(
                    expr, inner.map(type_arg(0)))
                self._mark_list_annotated(result, inner,
                                          inner_hint.get_element_type())
                self.ctx.set_expr_type(expr, result)
                return result
            # Recursive union type with a list member: propagate the union
            # as element hint so nested list literals infer as list[Tree]
            # (e.g. x: Tree = [1, [3, 4]] where type Tree = int | list[Tree])
            if inner_hint.needs_wrapper():
                list_member = _find_list_member(inner_hint)
                if list_member is not None:
                    result = self._analyze_array_literal(expr, inner)
                    self._mark_list_annotated(
                        result, inner.map(_find_list_member), inner_hint)
                    self.ctx.set_expr_type(expr, result)
                    return result

        # List comprehension with list type hint: propagate element type
        if isinstance(expr, TpyListComprehension):
            inner = self._bare_hint(slot)
            if is_list(inner.type) or is_array(inner.type):
                typ = self._analyze_list_comprehension(
                    expr, expected_elem=inner.map(type_arg(0)))
                self._mark_list_annotated(typ, inner, None)
                self.ctx.set_expr_type(expr, typ)
                return typ

        # Dict comprehension with dict type hint: propagate key/value types
        if isinstance(expr, TpyDictComprehension):
            inner = self._bare_hint(slot)
            if is_dict(inner.type):
                typ = self._analyze_dict_comprehension(
                    expr, key_hint=inner.map(type_arg(0)),
                    value_hint=inner.map(type_arg(1)))
                self.ctx.set_expr_type(expr, typ)
                return typ

        # Dict literal with dict type hint
        if isinstance(expr, TpyDictLiteral):
            inner = self._bare_hint(slot)
            if isinstance(inner.type, OptionalType) and is_dict(inner.type.inner):
                # A literal at an `Optional[dict]` slot is the dict member's:
                # the None alternative cannot be written as a literal, so the
                # member is the only annotation the key/value can take.
                inner = inner.map(optional_inner)
            inner_hint = inner.type
            if is_dict(inner_hint):
                result = self._analyze_dict_literal(
                    expr, inner.map(type_arg(0)), inner.map(type_arg(1)))
                self.ctx.set_expr_type(expr, result)
                return result
            if inner_hint.needs_wrapper():
                dict_member = _find_dict_member(inner_hint)
                if dict_member is not None:
                    result = self._analyze_dict_literal(
                        expr, inner.map(_find_dict_member).map(type_arg(0)),
                        inner)
                    self.ctx.set_expr_type(expr, result)
                    return result

        # Set comprehension with set type hint: propagate element type
        if isinstance(expr, TpySetComprehension):
            inner = self._bare_hint(slot)
            if is_set(inner.type):
                typ = self._analyze_set_comprehension(
                    expr, expected_elem=inner.map(type_arg(0)))
                self.ctx.set_expr_type(expr, typ)
                return typ

        # Non-empty set literal with set type hint
        if isinstance(expr, TpySetLiteral) and expr.elements:
            inner = self._bare_hint(slot)
            if is_set(inner.type):
                result = self._analyze_set_literal(
                    expr, inner.map(type_arg(0)))
                self.ctx.set_expr_type(expr, result)
                return result

        # Fall back to regular analysis, propagating hint through context
        # for functions that need it (e.g. unsafe_cast)
        with self.ctx.slot_hint_scope(slot):
            return self.analyze_expr(expr)

    def _analyze_name(self, expr: TpyName) -> TpyType:
        """Analyze a name reference."""
        # Use-after-consume: variable was consumed by a consuming method call
        if expr.name in self.ctx.func.consumed_vars:
            raise self.ctx.error(
                f"Cannot use '{expr.name}' after it was consumed by a consuming method call",
                expr,
            )
        # Any read of a bound coroutine counts as (potential) consumption
        # for the never-consumed warning.
        self.ctx.func.unread_coro_locals.pop(expr.name, None)

        # Before the namespace walk, which reaches the module level: a nested
        # def's name is a local of this scope from its start.
        self.ctx.check_nested_def_shadowed_read(expr.name, expr)

        # No ephemeral-borrow closure-capture check is needed: an escaping closure
        # is Callable-typed and captures by value (copies the borrow's value -- safe),
        # while a by-reference Fn-typed closure is inline / non-escaping (used within
        # the iteration step). So no closure can retain an ephemeral frame-slot borrow
        # past its step.

        # Check for INT type parameter references in generic class context
        # INT type params can be used as values in expressions (e.g., int32(N))
        if self.ctx.record_ctx.type_params and self.ctx.record_ctx.type_param_kinds:
            try:
                idx = self.ctx.record_ctx.type_params.index(expr.name)
                if self.ctx.record_ctx.type_param_kinds[idx] == TypeParamKind.INT:
                    # INT type param - return TypeParamRef with INT kind
                    # This represents a compile-time constant, treated as int32-compatible
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
                if (expr.name == "cls"
                        and binding.kind in (BindingKind.RECORD, BindingKind.ENUM)
                        and self.ctx.func.current_function is not None
                        and self.ctx.func.current_function.is_classmethod):
                    raise self.ctx.error(
                        "'cls' can only be used to construct ('cls(...)') or to "
                        "access class members ('cls.NAME') -- passing or returning "
                        "a class as a value needs 'type[T]', which is not supported yet",
                        expr)
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
                if (isinstance(result, OwnType)
                        and expr in self.ctx.all_last_uses
                        and not self.compat.demoted_by_hidden_borrow(expr)):
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
            if (expr not in self.ctx.all_last_uses
                    or self.compat.demoted_by_hidden_borrow(expr)):
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

    def _pending_decl_anchor(self, bind_stack: tuple[TpyStmt, ...]) -> TpyStmt:
        """The statement a loop-body-first local pre-declares in front of.

        Python locals are function-scoped, so the one declaration has to
        stand in a C++ scope enclosing BOTH the loop that binds the name and
        this read. That is the first enclosing statement the two do not
        share: inside the shared part they are in one block already, and
        anchoring on the loop itself would put the declaration inside a
        block the read is not in -- or, for a sibling loop, leave the first
        loop free to declare the name a second time.
        """
        read_stack = self.ctx.func.compound_stack
        shared = 0
        while (shared < len(bind_stack) and shared < len(read_stack)
               and bind_stack[shared] is read_stack[shared]):
            shared += 1
        return bind_stack[shared] if shared < len(bind_stack) else bind_stack[-1]

    def _promote_pending_loop_var(self, name: str) -> bool:
        """Promote a pending loop-scoped variable if present.

        Returns True if the variable was promoted: added to scope and
        registered for codegen pre-declaration. Whether it counts as
        assigned is not this read's call -- the binding site recorded that
        (when the loop provably ran) and the branch merges in between have
        already taken it back if only some arms bound it.
        """
        pending = self.ctx.func.pending_loop_vars.get(name)
        if pending is None:
            return False
        var_type, orig_stmt = pending.var_type, pending.head_stmt
        decl_stmt = self._pending_decl_anchor(pending.first_stack)
        # A body-declared local stays in the table: the promotion defines
        # it in the scope only, and the table is what the frame-local hoist
        # and its resolution sinks read -- popped, a local read after a
        # loop that suspends would be a case-block local read from another
        # case. The loop VARIABLE pops: its frame placement is the
        # for-head's own (hoist_loop_var below), a field here would be a
        # dead second one.
        if orig_stmt is not None:
            del self.ctx.func.pending_loop_vars[name]
        self.ctx.func.current_scope.define(name, var_type)
        # The one declaration stands at the anchor, which encloses this scope,
        # so the name's storage is no longer the loop body's: an alias taken
        # here does not outlive what it binds, and the escape check must not
        # read the binding's original depth and say it does.
        self.ctx.func.var_scope_depth[name] = self.ctx.func.current_scope.depth
        # Bound by a loop body that provably ran: the name is assigned here,
        # in the spelling the rest of sema reads.
        if name in self.ctx.func.loop_bound_assigned:
            self.ctx.func.definitely_assigned.add(name)
        # Register for codegen pre-declaration at the binding's anchor
        self.ctx.record_branch_decls(decl_stmt, {name: var_type})
        # A str/bytes view now declared outside the loop body stays a view
        # only over storage that outlives the body; a body local that is not
        # hoisted itself is out of scope here, so the rule owns over it.
        self.calls.deduction.own_hoisted_view(
            name, self.ctx.block_locals_of.get(decl_stmt, frozenset()))
        # Mark the original for-loop's var for hoisted codegen (hidden counter)
        if isinstance(orig_stmt, TpyForEach) and name == orig_stmt.var:
            orig_stmt.hoist_loop_var = True
        return True

    def sync_pending_loop_var_type(self, name: str, var_type: TpyType) -> None:
        """Write a promoted loop-body local's JOINED type back to the pending
        table and to its pre-declaration.

        The table is the authority every later promotion reads -- the block
        that did the joining binding hands its scope back at its end -- so a
        binding that widened the local has to leave the widened type there,
        or the next block (or the read after it) would promote the stale
        first type and the one declaration would render two types.
        """
        pending = self.ctx.func.pending_loop_vars.get(name)
        if (pending is None or pending.head_stmt is not None
                or pending.var_type == var_type):
            return
        self.ctx.func.pending_loop_vars[name] = dc_replace(
            pending, var_type=var_type)
        self.ctx.record_branch_decls(self._pending_decl_anchor(pending.first_stack),
                                     {name: var_type})

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

    def _select_default_type(self, t: TpyType,
                             warn_node: TpyExpr | None = None) -> TpyType:
        """The type a select operand has when nothing on the other side
        resolves it: literals and pending containers at their defaults."""
        if isinstance(t, IntLiteralType):
            return self.ctx.default_int_for_literal(t, warn_node)
        if isinstance(t, FloatLiteralType):
            return FLOAT
        if isinstance(t, PendingViewType):
            return t.family.owned_type
        return self._normalize_pending_container(t)

    def _select_type_for_message(self, t: TpyType,
                                 python_literals: bool = False) -> str:
        if not (python_literals and isinstance(t, IntLiteralType)):
            t = self._select_default_type(t)
        t = resolve_int_literals(t, BIGINT if python_literals
                                 else self.ctx.default_int_for_literal)
        if isinstance(t, NominalType) and any(
                isinstance(a, UnknownElementType) for a in t.type_args):
            # An empty literal has no element type yet; `[]` is a `list`.
            return t.name
        return str(t)

    def _select_types_for_message(self, t: TpyType,
                                  e: TpyType) -> tuple[str, str]:
        ts = self._select_type_for_message(t)
        es = self._select_type_for_message(e)
        if ts == es:
            # Only a literal can make two unjoinable operands read alike at
            # their defaults (`(4, "d")` beside a `tuple[int32, str]`); name
            # it by its Python type, `int`.
            ts = self._select_type_for_message(t, python_literals=True)
            es = self._select_type_for_message(e, python_literals=True)
        return ts, es

    def _slot_converts(self, verdict: InferredJoin,
                       slot: SlotHint | None) -> bool:
        """Whether a declared float slot takes a select's int/float mix as
        its float: only a top-level operand pair, never one below a
        `T | None` (the value may be None) nor at a position an inferred
        local decides."""
        return (not verdict.nested and not verdict.through_optional
                and not self._hint_converts_int(verdict.int_side, slot))

    def _analyze_select_operand(self, e: TpyExpr,
                                declared: SlotHint) -> TpyType:
        """Analyze a ternary arm against the declared type of the select's
        slot, so a literal pins to it before the join sees the arms. A
        container literal takes the declared type while it is analysed; a
        tuple literal keeps its literal leaves, so they pin to the declared
        elements here. A variable keeps its own type: nothing converts it."""
        t = self.analyze_expr_with_hint(e, declared)
        slot = declared.map(
            lambda d: unwrap_readonly(unwrap_own(unwrap_ref_type(d))))
        if (isinstance(e, TpyTupleLiteral) and isinstance(t, TupleType)
                and isinstance(slot.type, TupleType)
                and self.compat.is_type_compatible(t, slot.type)):
            t = self._resolve_literals_with_hint(t, slot)
            self.ctx.set_expr_type(e, t)
        return t

    def _select_join(self, t: TpyType, e: TpyType,
                     t_expr: TpyExpr, e_expr: TpyExpr) -> InferredJoin:
        """The one result type of a select -- `a if c else b`, `a or b`,
        `a and b` -- over operand types already stripped of Ref/Own. An
        inferred join, so an int meeting a float is refused (`type_join`).

        A literal or a pending container resolves against the OTHER operand,
        since the two must share one C++ type; readonly on either side stays
        on a reference result, which may alias that side."""
        def join(t: TpyType, e: TpyType) -> TpyType | InferredJoin | None:
            if t == e:
                return t
            joined = self._select_join_bare(unwrap_readonly(t),
                                            unwrap_readonly(e), t_expr, e_expr)
            if isinstance(joined, InferredJoin):
                return joined
            if (joined is not None and not joined.is_value_type()
                    and (isinstance(t, ReadonlyType)
                         or isinstance(e, ReadonlyType))):
                return ReadonlyType(joined)
            return joined
        return join_inferred_value_types(t, e, join)

    def _select_join_bare(self, t: TpyType, e: TpyType,
                          t_expr: TpyExpr,
                          e_expr: TpyExpr) -> TpyType | InferredJoin | None:
        if t == e:
            return t
        if isinstance(t, IntLiteralType) and isinstance(e, IntLiteralType):
            # Each literal at its own default, so `0 or 10**10` and
            # `10**10 or 0` both widen to the type that holds the big one.
            td = self.ctx.default_int_for_literal(t, t_expr)
            ed = self.ctx.default_int_for_literal(e, e_expr)
            return td if td == ed else widen_numeric_types(td, ed)
        if isinstance(t, IntLiteralType):
            if is_integer_type(e):
                return e
            t = self.ctx.default_int_for_literal(t, t_expr)
        if isinstance(e, IntLiteralType):
            if is_integer_type(t):
                return t
            e = self.ctx.default_int_for_literal(e, e_expr)
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
        # Both pending views of one family keep the pending type so view
        # deduction can chain the result back to the operands' resolution.
        if (isinstance(t, PendingViewType) and isinstance(e, PendingViewType)
                and t.family is e.family):
            return t
        if isinstance(t, PendingViewType):
            t = t.family.owned_type
        if isinstance(e, PendingViewType):
            e = e.family.owned_type
        if isinstance(t, PENDING_CONTAINER_TYPES) or isinstance(e, PENDING_CONTAINER_TYPES):
            return self._join_pending_containers(t, e)
        if t == e:
            return t
        return widen_numeric_types(t, e)

    def _join_pending_containers(
            self, t: TpyType, e: TpyType) -> TpyType | InferredJoin | None:
        """The select join of a pending container operand; an int/float mix
        between its elements and the other operand's comes back as that
        verdict, since the join's own pre-check does not descend a pending
        container beside a concrete one."""
        tn = self._normalize_pending_container(t)
        en = self._normalize_pending_container(e)
        if (isinstance(t, PENDING_CONTAINER_TYPES)
                and isinstance(e, PENDING_CONTAINER_TYPES)
                and self._unify_literal_types(t, e) is not None and tn == en):
            return tn
        for pending, target, swapped in ((t, en, False), (e, tn, True)):
            verdict = self._pin_select_pending(pending, target)
            if verdict.outcome is JoinOutcome.JOINED:
                return target
            if verdict.outcome is JoinOutcome.INT_FLOAT_MIX:
                return flipped(verdict) if swapped else verdict
        # A literal already pinned to its own declared type (`[2.5]` at a
        # `list[float]` slot) meets the other operand as that concrete type.
        return find_int_float_mix(tn, en)

    def _pin_select_pending(self, pending: TpyType,
                            target: TpyType) -> InferredJoin:
        """Resolve a pending container operand to the other operand's concrete
        container type, when it is that container spelled as a literal."""
        if (not isinstance(pending, PENDING_CONTAINER_TYPES)
                or isinstance(target, PENDING_CONTAINER_TYPES)
                or contains_pending_leaf(target)
                or any(isinstance(a, UnknownElementType)
                       for a in target.inner_types())
                or not self._pending_matches_hint(pending, target)):
            return InferredJoin(JoinOutcome.INCOMPATIBLE)
        deduction = self.calls.deduction
        if not self.compat.is_type_compatible(pending, target):
            # A float element beside an integer container is not assignable
            # at all, but it is the same int/float mix the pin names the
            # other way round.
            verdict = deduction.pin_pending_container(pending, target,
                                                      commit=False)
            return (verdict if verdict.outcome is JoinOutcome.INT_FLOAT_MIX
                    else InferredJoin(JoinOutcome.INCOMPATIBLE))
        return deduction.pin_pending_container(pending, target)

    def _commit_select(self, expr: TpyExpr, result: TpyType,
                       operands: tuple[tuple[str, TpyType], ...],
                       context: str) -> None:
        """Make every operand of a select take the joined type, so the C++
        select has one operand type (e.g. None -> std::optional<T>).
        An and/or passes no operands: its value-select lowering casts mixed
        scalar operands itself, and a coercion would also reach an and/or
        that is only a condition."""
        if is_list(result):
            # A fixed-size literal operand would become an Array of its own
            # size; the operands must share the one `list` type.
            for pt in collect_pending_source_types(self.ctx, expr):
                if isinstance(pt, PendingListType):
                    info = self.ctx.list_literals.get(pt.literal_id)
                    if info is not None:
                        info.needs_list_type = True
        for attr, operand_type in operands:
            # A readonly qualifier on the result only restricts the use.
            if unwrap_readonly(operand_type) != unwrap_readonly(result):
                setattr(expr, attr, self.compat.coerce_expr(
                    getattr(expr, attr), operand_type, result,
                    context, coercion_ctx=CoercionContext.INIT))

    def _logical_op_result_type(self, expr: TpyBinOp, left: TpyType,
                                right: TpyType,
                                declared_slot: SlotHint | None = None) -> TpyType:
        """Result type of `a and b` / `a or b`: the operand it yields, joined
        like a ternary's arms. Falls back to bool (the C++ &&/|| value) when
        the operands have no common type or either is a bool, which a truth
        test takes operand by operand; an int/float pair is refused unless
        the node is a truth test or a declared float slot converts it."""
        if is_bool_type(left) or is_bool_type(right):
            return BOOL
        lt = unwrap_own(unwrap_ref_type(left))
        rt = unwrap_own(unwrap_ref_type(right))
        if any(isinstance(unwrap_readonly(x), (NoneType, OptionalType, PtrType))
               or is_union_or_optional_type(unwrap_readonly(x))
               for x in (lt, rt)):
            # A nullable operand's select typing is not designed yet.
            return lt if lt == rt else BOOL
        verdict = self._select_join(lt, rt, expr.left, expr.right)
        result = verdict.joined
        if verdict.outcome is JoinOutcome.INT_FLOAT_MIX:
            slot_float = declared_float_slot(
                declared_slot.type if declared_slot else None)
            if (slot_float is not None and self._slot_converts(
                    verdict, declared_slot)):
                result = slot_float
            elif expr not in self.ctx.truth_test_selects:
                op = "and" if expr.op == "&&" else "or"
                raise self.ctx.error(select_mix_message(
                    verdict, f"`{op}`", lambda ls, rs: f"{ls} {op} {rs}",
                    expr.left, expr.right, self._mix_operand_types(lt, rt),
                    expr is self.ctx.name_initializer), expr)
        if result is None:
            return BOOL
        # The operands are not coerced, but a literal must still fit the
        # joined type: `u or 300` over a uint8 would otherwise wrap.
        for operand, operand_type in ((expr.left, lt), (expr.right, rt)):
            if isinstance(operand_type, IntLiteralType):
                self.compat.check_type_compatible(
                    operand_type, result, "logical operand", operand.loc,
                    source_expr=operand, coercion_ctx=CoercionContext.INIT)
        self._commit_select(expr, result, (), "logical operand")
        return result

    def _analyze_binop(self, expr: TpyBinOp,
                       declared_slot: SlotHint | None = None) -> TpyType:
        """Analyze a binary operation. `declared_slot` is the declared type
        an and/or's value goes to; each operand is analysed against it."""
        def operand(e: TpyExpr) -> TpyType:
            if declared_slot is not None:
                return self.analyze_expr_with_hint(e, declared_slot)
            if expr.op in ("&&", "||"):
                with self._forward_fill(expr, e):
                    return self.analyze_expr(e)
            return self.analyze_expr(e)
        left_type = operand(expr.left)
        if expr.op in ("&&", "||"):
            type_true, type_false = self.narrowing.condition_type_facts(expr.left)
            saved_types = dict(self.ctx.func.narrowed_types)
            # Save definitely_assigned: RHS may not execute due to short-circuit
            saved_assigned = frozenset(self.ctx.func.definitely_assigned)
            if expr.op == "&&":
                self.ctx.func.narrowed_types.update(type_true)
            else:
                self.ctx.func.narrowed_types.update(type_false)
            self.ctx.cond_operand_depth += 1
            try:
                right_type = operand(expr.right)
            finally:
                self.ctx.cond_operand_depth -= 1
                self.ctx.func.narrowed_types = saved_types
                # Rollback: RHS walrus vars are not definitely assigned
                self.ctx.func.definitely_assigned = set(saved_assigned)
        elif expr.cond_right:
            self.ctx.cond_operand_depth += 1
            try:
                right_type = self.analyze_expr(expr.right)
            finally:
                self.ctx.cond_operand_depth -= 1
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
                    f"Cannot compare {left_type} and {right_type} with '{op_spelling(expr.op)}'",
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
                    f"Operator '{op_spelling(expr.op)}' not supported for enum type '{enum_name}'",
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
                                f"'{op_spelling(expr.op)}')",
                                expr,
                            )
                        self._validate_comparison(expr, lt, rt)
                return BOOL
            else:
                raise self.ctx.error(
                    f"Operator '{op_spelling(expr.op)}' is not supported for tuple types",
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
            # A readonly haystack dispatches to the same `__contains__` as a
            # mutable one (the method-call receiver rule), which then must be
            # readonly itself.
            right_type = unwrap_send_sync(unwrap_ref_type(right_type))
            right_type = unwrap_readonly(right_type)
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
                        # `x in h` is an `h.__contains__(x)` call on h.
                        credit_implicit_receiver_call(
                            self.ctx, expr.right, right_type, original_method,
                            "__contains__", expr)
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
                        # __contains__ takes readonly[K] (the key is only looked
                        # up); membership cares about the bare key type, so unwrap
                        # for both the check and the diagnostic.
                        param_type = unwrap_readonly((int_overload or subst_overloads[0]).params[0].type)
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
                # For string containers, LHS must be str or char
                if is_any_str_type(right_type):
                    if not (is_any_str_type(left_type) or is_char_type(left_type)):
                        raise SemanticError(
                            f"Cannot check '{left_type}' membership in str (expected str or char)",
                            expr.loc
                        )
                else:
                    # Non-string collections compare each ITERATED element
                    # (a dict's key, not its value) with ==.
                    elem_type = helper.get_iterable_element_type_or_none(
                        right_type)
                    if elem_type is not None:
                        equatable = NominalType("Equatable", is_protocol=True)
                        if not self.protocols.type_conforms_to_protocol(elem_type, equatable):
                            raise self.ctx.error(
                                f"'in' requires element type '{elem_type}' to "
                                f"conform to 'Equatable' (no '__eq__' method)",
                                expr)
                    # With no `__contains__`, `x in h` iterates h: an
                    # implicit `h.__iter__()` call, as a `for` over it is.
                    # A readonly haystack is not rejected here (the readonly
                    # rule for an implicit `__iter__` is an open decision).
                    iter_recv = unwrap_readonly(right_type)
                    if (isinstance(iter_recv, NominalType)
                            and iter_recv.is_user_record):
                        _record_iter_receiver_mutation(
                            self.ctx, expr.right, iter_recv)
                return BOOL
            raise self.ctx.error(f"Cannot use '{op_spelling(expr.op)}' with non-iterable type {right_type}", expr)

        # Logical operators: Python semantics returns an operand, not bool.
        if expr.op in ("&&", "||"):
            return self._logical_op_result_type(expr, left_type, right_type,
                                                declared_slot)

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
            # (the concat stub returns Own[list[T]]; unwrap to see the list)
            if is_list(unwrap_own(result.method.return_type)):
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
                                is_readonly=method.is_readonly,
                                owning_type_qname=method.owning_type_qname,
                                # Borrow facts (return_borrows_from) land on the
                                # registered fi after body analysis; keep the
                                # chain so consumers can reach them via .root.
                                canonical_fi=method.root,
                            )
                            expr.resolved_binop = ResolvedBinop(
                                method=resolved_method,
                                left_wrapper="{expr}",
                                right_wrapper="{expr}",
                                receiver_type=left_effective,
                            )
                            return ret_type

        raise SemanticError(
            f"Invalid operand types for '{op_spelling(expr.op)}': {left_type} and {right_type}",
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
            mark_pending_list_mutated(self.ctx, sub_expr, sub_type)

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
        for i, (op, comp) in enumerate(zip(expr.ops, expr.comparators)):
            # Only the first two operands always evaluate; every later
            # comparator sits behind a compare that has to pass.
            pair = TpyBinOp(prev, op, comp, loc=expr.loc, cond_right=i >= 1)
            self.analyze_expr(pair)
            pairs.append(pair)
            prev = comp
        expr.pairs = pairs
        return BOOL

    def _analyze_unaryop(self, expr: TpyUnaryOp) -> TpyType:
        """Analyze a unary operation."""
        operand_type = (self.analyze_condition(expr.operand) if expr.op == "!"
                        else self.analyze_expr(expr.operand))
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
            # An and/or operand is tested operand by operand, as in an `if`,
            # whatever the one type its value has.
            if (isinstance(expr.operand, TpyBinOp)
                    and expr.operand.op in ("&&", "||")):
                return BOOL
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
                # Bitwise not on literal - treat as int32
                # Still resolve for codegen
                if result := self.operators.resolve_unaryop(effective_type, expr.op, loc_node=expr):
                    expr.resolved_unaryop = result
                return INT32

        # Use registry for unary operators
        if result := self.operators.resolve_unaryop(effective_type, expr.op, loc_node=expr):
            expr.resolved_unaryop = result
            return result.method.return_type

        raise self.ctx.error(f"Invalid operand type for unary '{op_spelling(expr.op)}': {operand_type}", expr)

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
                    msg = (f"Comparison '{op_spelling(expr.op)}' on '{check_type}': "
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
            record = self.ctx.registry.receiver_record(typ)
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
                expr.accessed_field_is_interior = field_info.is_interior_mutable
                expr.native_field_name = field_info.native_name
                field_type = field_info.type
                if type_subst:
                    field_type = self.type_ops.substitute_type_params(field_type, type_subst)
                return field_type
            # Check properties (getter access)
            prop = self.protocols.lookup_record_property(record, expr.field)
            if prop is not None:
                expr.resolved_property_getter = prop.getter
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
        # Inheritance with fixed type-args (`class Child(C[int32]): pass;
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
        record_info = self.ctx.class_record_of(binding)
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
        # and point the user at instance access. `Class[int32].X` syntax
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
                f"'{record_info.display_name}' is not an ancestor of '{current_rec.display_name}'; "
                f"cannot access '{record_info.display_name}.{expr.field}' here",
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
        expr.native_field_name = field_info.native_name
        return make_ref(field_type)

    def _analyze_field_access(self, expr: TpyFieldAccess,
                             obj_type: TpyType | None = None) -> TpyType:
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
                        expr.enum_member_of = enum_type
                        return enum_type
                    companion = self.ctx.registry.receiver_record(enum_type)
                    if (companion is not None
                            and companion.get_method_overloads(expr.field)):
                        raise self.ctx.error(
                            f"'{enum_type.name}.{expr.field}' is a method; an enum "
                            f"method cannot be used as a value yet -- call it",
                            expr)
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
                        # Same fact the TpyName-receiver arm stamps: a
                        # type-level member access (`Outer.Kind.TEXT`), so
                        # downstream consumers need not re-derive the chain.
                        expr.enum_member_of = chain_type
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

        if obj_type is None:
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

        # Enum instance property access: c.name -> StrView (a view into
        # EnumUtil's static member-name storage -- safe to hold; owned sinks
        # copy via the standard view->owned machinery), c.value -> underlying type
        if is_enum_type(actual_type):
            if expr.field == "name":
                return STRVIEW
            elif expr.field == "value":
                return enum_info_of(actual_type).underlying_type
            # A property of the enum's companion falls through to the record
            # lookup below; a plain method read is not a value yet.
            companion = self.ctx.registry.receiver_record(actual_type)
            if (companion is not None
                    and self.protocols.lookup_record_property(companion, expr.field) is not None):
                pass
            elif companion is None or not companion.get_method_overloads(expr.field):
                raise self.ctx.error(
                    f"Enum value of type '{actual_type.name}' has no attribute '{expr.field}'. "
                    f"Use '{actual_type.name}.{expr.field}' to access enum members", expr)
            else:
                raise self.ctx.error(
                    f"'{expr.field}' is a method of enum '{actual_type.name}'; "
                    f"an enum method cannot be used as a value yet -- call it",
                    expr)

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
                # An `unsafe_interior_mutable` field is outside the readonly boundary: it keeps
                # its declared (mutable) shape so refcount-style bookkeeping can
                # be touched through a readonly receiver (the C++ `mutable`-via-
                # raw-pointer pattern). Reassigning the slot is still rejected --
                # that is enforced on the receiver, not here.
                if is_readonly_obj and not expr.accessed_field_is_interior:
                    if isinstance(result, PtrType) and not result.is_readonly:
                        result = result.as_const()
                    elif is_span(result) and not is_readonly_span(result):
                        result = span_as_const(result)
                    elif not result.is_value_type():
                        result = ReadonlyType(unwrap_readonly(result))
                # A consuming method (self: Own[Self]) moves a field out only
                # where its return value may (see consuming_return_fields);
                # anywhere else the read is an ordinary borrow.
                expr.consuming_move = (
                    self.ctx.in_consuming_method
                    and isinstance(expr.obj, TpyName) and expr.obj.name == "self"
                    and expr.field in self.ctx.func.consuming_return_fields)
                if (expr.consuming_move and not is_readonly_obj
                        and not result.is_value_type()):
                    result = OwnType(result)
                # Apply field path narrowing (e.g. after `if obj.field is not None:`)
                field_key = _expr_to_narrowing_key(expr)
                if field_key is not None:
                    narrowed = self.ctx.func.narrowed_types.get(field_key)
                    if narrowed is not None:
                        result = narrowed
                if (expr.resolved_property_getter is not None
                        and not expr.is_write_target):
                    # A property read IS the getter call, so it becomes one --
                    # here, at the END of the read analysis, because everything
                    # above still reads the node as a field access: the
                    # deref/narrowing block and the field-path narrowing key
                    # spell `obj.prop` off `expr.field`. From this point on
                    # every position answers for it at its call arm, with no
                    # property-specific code.
                    become_method_call(
                        expr, method=expr.field, args=[],
                        fi=expr.resolved_property_getter)
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
            hint = ""
            missing_in = self.ctx.registry.get_record_for_type(actual_type)
            if (missing_in is not None and missing_in.is_return_exception
                    and any(f.name == expr.field
                            for anc in self.ctx.registry.iter_ancestor_records(missing_in)
                            for f in anc.fields)):
                # CPython reads it off the thrown Exception base.
                hint = (f": a return-only exception (ReturnException) carries "
                        f"only the fields it declares; declare '{expr.field}' "
                        f"on '{actual_type.name}' to use it")
            raise self.ctx.error(
                f"Record '{actual_type.name}' has no field '{expr.field}'{hint}", expr)
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

    def _materialize_narrowing_element_coercion(
        self, elem: TpyExpr, actual: TpyType, expected: TpyType,
        ctx_msg: str, target_is_storage_form: bool,
    ) -> TpyExpr:
        """`compat.materialize_fresh_value` for an aggregate-literal element
        whose caller holds no resolved coercion (the tuple-literal hint path,
        which validates the element against the whole tuple afterwards).
        Caller-side validation still rejects genuinely incompatible
        elements."""
        # A literal's element slot owns what it is handed, so a borrow-only
        # rule does not apply here.
        coercion = resolve_coercion(
            unwrap_own(actual), unwrap_own(expected), CoercionContext.INIT,
            sink_owns=True)
        return self.compat.materialize_fresh_value(
            elem, actual, expected, ctx_msg, coercion,
            CoercionContext.INIT,
            target_is_storage_form=target_is_storage_form)

    def _materialize_fresh_value_elements(
        self, nodes: list[TpyExpr], types: list[TpyType],
        expected: TpyType | None, ctx_label: str,
    ) -> None:
        """Spell a fresh-value element coercion onto each element of a set /
        dict literal analysed against a slot, retyping the element to the slot
        it converts into. The array literal does this inside its contextual
        per-element check; the set and dict literals have no such pass, so
        without it their elements peer-unify to the SOURCE type and the decl
        rejects a value the same slot takes elsewhere (`{s[0]}` at `set[str]`,
        where `[s[0]]` at `list[str]` compiles)."""
        if expected is None:
            return
        for i, elem_type in enumerate(types):
            new_elem = self._materialize_narrowing_element_coercion(
                nodes[i], elem_type, expected, f"{ctx_label} {i + 1}",
                target_is_storage_form=True)
            if new_elem is not nodes[i]:
                nodes[i] = new_elem
                types[i] = expected

    def _comp_elem_moves(self, gen: TpyComprehensionGenerator, elem: TpyExpr,
                         is_last_sink: bool = False) -> bool:
        """Whether a comprehension sink is the consuming MOVE -- an owned-source
        bare-loop-var sink. The LAST-evaluated sink (list/set element, dict
        value) is structurally a last use: the loop var is rebound each
        iteration and every earlier read (filter, dict key) is sequenced before
        it, so it moves unconditionally. An EARLIER sink (the dict key) gates on
        `all_last_uses` -- it may not move if a later sink reads the var
        (`{node: node.id}` must not move the key). Codegen mirrors this exactly
        (a bare-var last sink std::move'd directly; earlier/sub-expression sinks
        via `_maybe_move`), so the suppress/move decisions cannot drift."""
        inner = elem
        while isinstance(inner, TpyCoerce):
            inner = inner.expr
        return (gen.owns_elements
                and isinstance(inner, TpyName)
                and inner.name == gen.var
                and (is_last_sink or inner in self.ctx.all_last_uses))

    def _warn_storage_element_copy(self, elem: TpyExpr, elem_type: TpyType) -> None:
        """A reference-type container-literal element is stored by value (the
        container owns its elements -- storage form), so an lvalue source is
        copied, diverging from CPython aliasing. Fire the same diagnostic the
        `.append`/`.insert` path emits, routed through check_type_compatible's
        T -> Own[T] branch (which returns no coercion node -- the warning and
        last-use/copy() suppression are the only effects). A value type,
        rvalue, copy(), or last-use auto-move does not warn. A generic element
        reaches the same path: the verdict is unknowable here, so -- like
        `.append` -- it is recorded and answered at the instantiation."""
        bare = unwrap_ref_type(unwrap_own(unwrap_readonly(elem_type)))
        # Not gated on has_pointer_repr_element: a literal's members come back
        # Own-wrapped (storage form, not pointer-repr), so the dispatch unwraps
        # per member rather than testing the tuple type as a whole.
        if isinstance(bare, TupleType):
            self.compat.warn_storage_tuple_copy(elem, bare, "owned storage")
            return
        if bare.is_value_type():
            return
        self.compat.check_type_compatible(
            bare, OwnType(bare), "container literal element",
            elem.loc, source_expr=elem)

    def _analyze_array_literal(
        self, expr: TpyArrayLiteral, expected: SlotHint | None = None
    ) -> TpyType:
        """Analyze an array literal [expr, expr, ...]

        In function-local contexts, returns a PendingListType that will be
        resolved to Array or list based on usage (mutation, parameter passing).
        In global/module context, returns ListType directly.

        Args:
            expected_elem: When provided (from a type annotation or return type hint),
                each element is checked against this type instead of against the first
                element. Enables mixed-type literals like [int32(1), None] when the
                annotation is list[int32 | None].
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

        expected_elem = expected.type if expected is not None else None
        # Analyze all elements, propagating expected type as hint when available
        if expected is not None:
            elem_types = [self.analyze_expr_with_hint(e, expected)
                          for e in expr.elements]
        else:
            elem_types = [self.analyze_expr(e) for e in expr.elements]

        # Strip OwnType wrappers -- _apply_own_wrapper marks non-last-use
        # refs as Own[T], but element type compatibility must compare the
        # underlying types (codegen handles the move/copy distinction).
        elem_types = [unwrap_own(t) for t in elem_types]

        # As for a dict or set literal: an element the hint would convert
        # keeps its own type, and the literal is joined like an unhinted one.
        if self._converting_element(elem_types, expected) is not None:
            expected_elem = None
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
                    exp_s, act_s = disambiguated_pair(expected_elem, elem_type)
                    raise self.ctx.error(
                        f"List literal element {i} has type {act_s}, "
                        f"incompatible with annotated element type {exp_s}", expr
                    )
                try:
                    coercion = self.compat.check_type_compatible(
                        elem_type, expected_elem,
                        f"array literal element {i}",
                        expr.loc,
                        source_expr=expr.elements[i - 1],
                        target_is_storage_form=True,
                    )
                except SemanticError:
                    exp_s, act_s = disambiguated_pair(expected_elem, elem_type)
                    raise self.ctx.error(
                        f"List literal element {i} has type {act_s}, "
                        f"incompatible with annotated element type {exp_s}", expr
                    )
                expr.elements[i - 1] = self.compat.materialize_fresh_value(
                    expr.elements[i - 1], elem_type, expected_elem,
                    f"array literal element {i}", coercion,
                    CoercionContext.INIT, target_is_storage_form=True)
        else:
            # Inferred mode: check all elements against first element's type
            first_type = elem_types[0]
            # Keep IntLiteralType so array can coerce to either int32 or BigInt based on context

            for i, elem_type in enumerate(elem_types[1:], 2):
                # Literal-aware unification (int/float literals, tuples, nested
                # pending containers); the demotion hook inside converges jagged
                # pending lists -- bare or wrapped in a tuple -- to one C++ type.
                verdict = join_inferred_value_types(
                    first_type, elem_type, self._unify_literal_types)
                if verdict.outcome is JoinOutcome.JOINED:
                    first_type = verdict.joined
                    continue
                if verdict.outcome is JoinOutcome.INT_FLOAT_MIX:
                    if self._hint_gave_float(verdict, expr.elements[:i],
                                             expected):
                        first_type = (first_type if verdict.int_first
                                      else elem_type)
                        continue
                    raise self.ctx.error(literal_mix_message(
                        verdict, "List literal has mixed types", "element",
                        i, elem_type, first_type, expr.elements[:i],
                        self._name_target_annotation(
                            expr, lambda f: f"list[{f}]")), expr)
                # Nested lists with IntLiteralType elements are compatible
                if (is_list(first_type) and is_list(elem_type) and
                    isinstance(first_type.element_type, IntLiteralType) and
                    isinstance(elem_type.element_type, IntLiteralType)):
                    continue
                if elem_type != first_type:
                    ft = self._user_type_name(first_type)
                    et = self._user_type_name(elem_type)
                    raise self.ctx.error(
                        f"List literal has mixed types: element {i} is {et}, "
                        f"but earlier elements are {ft}. "
                        f"Use a type annotation like list[{ft} | {et}]", expr
                    )

        # Only user-written literals warn: a macro/compiler-synthesized literal
        # (asdict/astuple field copies, etc.) has no source loc and its copies
        # aren't user-controllable. An `Any` element slot already warns via the
        # dedicated copies-into-Any path -- don't double-warn.
        if expr.loc is not None and not isinstance(expected_elem, AnyType):
            for elem, et in zip(expr.elements, elem_types):
                self._warn_storage_element_copy(elem, et)

        # A container owns its elements, so an inferred TUPLE element type takes
        # the owned storage form -- the same type the annotation rule forces the
        # user to spell (`list[tuple[Box, Box]]`; `Own` there is rejected as
        # redundant). Left uncollapsed, the element keeps its `Own`/`Ref`
        # markers, which to_cpp() renders as reference members
        # (`std::tuple<Box, Box&>`) -- so the same store aliased under a fixed
        # Array and copied under a vector, purely by which one inference picked.
        first_type = owned_tuple_storage_type(first_type)

        size = len(expr.elements)

        # Global context (no current function) -> ListType (std::vector)
        # Keep IntLiteralType to allow coercion to int32 when annotation is present
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
            inner = self._unwrap_awaitable_return(source_fi.return_type)
            expr.await_result_is_borrow = async_result_aliases(
                source_fi.async_inner_return, inner)
            return inner

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
                    expr.awaited_method_owner_type = coro_struct_owner(
                        mfi.owning_type_qname, owner_type,
                        self.ctx.registry.get_record_for_type(owner_type))
                if mfi.return_type is not None:
                    self.ctx.func.current_awaited_subframes.append(mfi)
                    inner = self._unwrap_awaitable_return(mfi.return_type)
                    expr.await_result_is_borrow = async_result_aliases(
                        mfi.async_inner_return, inner)
                    return inner
        # An Own[Task[T]] rvalue (e.g. `await asyncio.create_task(...)`)
        # is a valid await operand -- strip the Own[] before structural
        # matching so the inner Task[T] / Awaitable conformance check fires.
        unwrapped = unwrap_own(unwrap_ref_type(operand_type))
        if isinstance(unwrapped, NominalType):
            # Bound coroutine handle (owned-erased Own[Cancellable[T]]
            # local/param): erased await through the protocol's virtual
            # __poll__. Single-use: the handle is consumed, so a later
            # read (second await, create_task) is a compile error --
            # CPython's runtime "cannot reuse already awaited coroutine"
            # surfaced at compile time.
            # Concrete coroutine handle (zero-alloc representation): the
            # await polls the handle's own frame slot in place -- no
            # sub-future field, no allocation. Must precede the erased
            # branch below (ConcreteCoroType carries the same qname).
            if isinstance(unwrapped, ConcreteCoroType):
                if not isinstance(operand, TpyName):
                    raise self.ctx.error(
                        "await of a coroutine-handle expression must be "
                        "a named binding; bind it to a variable first",
                        expr)
                self._check_coro_await_consume(operand, expr)
                expr.awaited_prebuilt_slot = operand.name
                # Erased-equivalent conservative frame classification.
                self.ctx.func.current_awaited_subframes.append(None)
                self._mark_await_operand_mutated(operand)
                expr.await_result_is_borrow = unwrapped.result_is_borrow
                return unwrapped.type_args[0]
            # Cancellable only: the structural Awaitable[T] has no @dynamic
            # C++ base to poll through, so it stays rejected below.
            if (unwrapped.is_protocol
                    and unwrapped.qualified_name() == qnames.CANCELLABLE
                    and len(unwrapped.type_args) == 1
                    and isinstance(unwrapped.type_args[0], TpyType)):
                expr.awaited_task_inner = unwrapped.type_args[0]
                # Erased frame: sema cannot classify the stored frame -- non-Send.
                self.ctx.func.current_awaited_subframes.append(None)
                self._mark_await_operand_mutated(operand)
                if isinstance(operand, TpyName):
                    self._check_coro_await_consume(operand, expr)
                return unwrapped.type_args[0]
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

    def _check_coro_await_consume(self, operand: TpyName, expr: TpyAwait) -> None:
        """Awaiting a bound coroutine consumes it. Mirrors the
        consuming-method loop guard: consumption inside a loop of a
        handle bound outside it would re-poll a finished coroutine on
        the next iteration."""
        if self.ctx.func.loop_depth > 0:
            var_depth = self.ctx.func.var_scope_depth.get(operand.name, 0)
            if var_depth < self.ctx.func.current_scope.depth:
                raise self.ctx.error(
                    f"Cannot await coroutine '{operand.name}' inside a "
                    f"loop; the handle is single-use and is not re-bound "
                    f"each iteration",
                    expr,
                )
        self.ctx.func.consumed_vars.add(operand.name)
        # Awaiting an Own[Cancellable[T]] PARAM is consumption -- without
        # this the never-consumed-Own-param warning false-positives.
        self.ctx.mark_own_param_consumed(operand.name)

    def _mark_await_operand_mutated(self, operand) -> None:
        """`await x` is, for mutation purposes, an `x.__poll__(waker)` call
        on a non-readonly `__poll__`. An rvalue operand (fresh coro from
        `await f()`) has no durable root to credit, but a readonly one still
        rejects. With no `__poll__` FunctionInfo (a coroutine handle) the
        mark is eager.
        """
        if _root_name_of_expr(operand) is None:
            check_implicit_readonly_receiver(self.ctx, operand, None,
                                             "__poll__", operand)
            return
        operand_type = self.ctx.get_expr_type(operand)
        bare = (unwrap_readonly(unwrap_own(unwrap_ref_type(operand_type)))
                if operand_type is not None else None)
        credit_implicit_receiver_call(self.ctx, operand, bare, None, "__poll__",
                                      operand)

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
        # so `Future[int32]` returns int32, not the type-var T.
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

        # A `global`-declared target writes the MODULE variable, like the plain
        # assignment path -- the global lives in `global_scope`, which is not in
        # the function's scope chain, so without this the binding below would
        # define a function-local shadow and the write would be lost.
        if name in self.ctx.func.global_declarations:
            return self._analyze_global_walrus(expr, name, value_type)

        # Resolve pending/literal types for the variable binding
        resolved = value_type
        if isinstance(resolved, IntLiteralType):
            resolved = self.ctx.default_int_type
        elif isinstance(resolved, FloatLiteralType):
            resolved = FLOAT
        elif isinstance(resolved, PendingViewType):
            resolved = resolved.family.owned_type

        # Mirror the VarDecl path: a reassigned per-element-Own tuple local takes
        # the unified borrow type (Own[T] element -> T) so the walrus first
        # binding agrees with later rebinds / narrowed accesses, rather than
        # keeping owning storage while an access expects pointer-repr.
        if name in self.ctx.func.current_reassigned_vars:
            resolved = collapse_tuple_own_elements(resolved)

        # Mirror the VarDecl inferred-local type: a binding's local type is
        # Own-stripped (owned-vs-borrow is tracked via owned_locals /
        # stmt_borrow_decls, not by keeping Own[T] as the type). Without this,
        # an lvalue source typed Own[T] (e.g. an owned local) makes
        # unwrap_readonly(resolved).is_value_type() short-circuit the borrow
        # classification below, so the walrus copies into owned storage instead
        # of aliasing the source.
        resolved = unwrap_own(resolved)

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
            # Mirror the VarDecl ownership classification for non-value
            # bindings: a fresh rvalue is owned (feeds the branch-decl owned
            # form and last-use moves); a borrow records the statement-level
            # borrow fact and registers the alias for mutation tracking,
            # exactly like `v = h.view()` would.
            self.ctx.func.bind_kinds[expr] = bind_kind_of(self.ctx, expr.value)
            # The walrus binds a fresh local, so its literal members take the
            # local sink's copy rule, as the decl `u = (1, (2, c))` does.
            bound_lit = peel_value_wrappers(expr.value)
            bound_tuple = unwrap_readonly(resolved)
            if (isinstance(bound_lit, TpyTupleLiteral)
                    and isinstance(bound_tuple, TupleType)):
                self.compat.check_tuple_literal_members(
                    bound_lit, bound_tuple, TupleSink.LOCAL, "owned storage")
            if not unwrap_readonly(resolved).is_value_type():
                if holds_generator_object(resolved):
                    record_frame_binding_roots(
                        self.ctx, name, frame_binding_fact(expr.value), expr)
                if is_rvalue_source(self.ctx, expr.value):
                    note_owned_local(self.ctx, name, resolved)
                else:
                    record_stmt_borrow_binding(self.ctx, name, resolved, expr.value)
                    register_binding_borrow(self.ctx, name, expr.value)
                    val_inner = (expr.value.expr
                                 if isinstance(expr.value, TpyCoerce)
                                 else expr.value)
                    # Local import: statements imports this module.
                    from .statements import _register_call_result_borrow
                    _register_call_result_borrow(self.ctx, name, val_inner)
            # For a collapsed per-element-Own borrow tuple, the walrus result
            # must be the collapsed type so `(t := ...)[i]` element access
            # agrees with the pointer-repr decl; other bindings keep the raw
            # value type (preserving literal-ness etc.).
            res_bare = unwrap_readonly(resolved)
            if (isinstance(res_bare, TupleType)
                    and res_bare.has_pointer_repr_element()):
                result_type = resolved
            else:
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
            bt.rebind_borrower(name, expr.value)
            # Local import avoids the statements <-> expressions cycle.
            from .statements import _register_tuple_binding_borrows
            _register_tuple_binding_borrows(
                self.ctx, name, expr.value, facts_type)
        # An alias of an ephemeral borrow is the same stale-slot borrow under
        # another name (mirrors the VarDecl _update_ephemeral_alias_fact).
        eph = self.ctx.func.ephemeral_borrow_vars
        eph_root = (ephemeral_borrow_root(eph, expr.value)
                    if (not resolved.is_value_type() or is_borrow_tuple)
                    else None)
        if eph_root is not None:
            eph[name] = eph[eph_root]
        else:
            eph.pop(name, None)

        return result_type

    def _analyze_global_walrus(
        self, expr: TpyNamedExpr, name: str, value_type: TpyType,
    ) -> TpyType:
        """Analyze `(g := v)` where `g` is `global`-declared: a write to the
        module variable, mirroring the var-decl global arm (coerce to the
        global's declared type, no local binding). Codegen keys the matching
        assign-to-global render on `global_declared_vars`."""
        global_type = self.ctx.global_scope.lookup(name)
        if global_type is None:
            raise self.ctx.error(
                f"name '{name}' is not defined at module level", expr)
        inner_global = unwrap_readonly(global_type)
        if global_binds_by_reference(inner_global):
            from .statements import global_rebind_message
            raise self.ctx.error(
                global_rebind_message(name, inner_global), expr)
        # Same exclusion the walrus local-reassign arm makes: a borrow-form
        # tuple needs the var-decl path's storage lift, which the inline
        # assign has no place to put.
        if (isinstance(inner_global, TupleType)
                and (inner_global.has_pointer_repr_element()
                     or inner_global.has_own_element())):
            raise self.ctx.error(
                f"walrus reassignment of global '{name}' of type "
                f"'{collapse_tuple_own_elements(inner_global)}' is not "
                f"supported yet; use a separate assignment statement", expr)
        _, expr.value = self.compat.coerce_reassignment(
            name, global_type, value_type, expr.value, expr)
        # Mirror the var-decl global arm: the global scope carries the write,
        # the function scope carries the binding reads and narrowing resolve
        # against. Codegen still renders the write against the module variable
        # (it keys on `global_declared_vars`, not on this binding).
        self.ctx.global_scope.define(name, global_type)
        self.ctx.func.current_scope.define(name, global_type)
        self.narrowing.update_after_write(name, global_type, value_type,
                                          expr.value)
        # The write invalidates views and borrows of the global's storage the
        # same way the var-decl path's does -- without this a view pinned to
        # the old string/span survives the rebind unwarned.
        # Local import: statements <-> expressions circular dodge.
        from .statements import _handle_pinned_view_rebind
        self.ctx.mark_all_view_borrowers_mutated(name)
        _handle_pinned_view_rebind(self.ctx, name, expr)
        bt = self.ctx.func.borrow_tracker
        bt.retarget_storage_borrows(name)
        bt.remove_borrower(name)
        return global_type

    def _analyze_if_expr(
        self, expr: TpyIfExpr, type_hint: SlotHint | None = None,
    ) -> TpyType:
        """Analyze a ternary conditional: then_expr if condition else else_expr."""
        self.analyze_condition(expr.condition)
        self.narrowing.warn_truthy_value_optionals(expr.condition)

        then_facts, else_facts = self.narrowing.condition_type_facts(
            expr.condition)

        # Save narrowed_types (ternary doesn't create vars, so we only
        # need to save/restore narrowing, not the full InitTracker state).
        saved_narrowed = dict(self.ctx.func.narrowed_types)

        self.ctx.func.narrowed_types.update(then_facts)
        # Both arms are conditionally evaluated; only the condition above is
        # not.
        self.ctx.cond_operand_depth += 1
        try:
            if type_hint is not None:
                then_type = self._analyze_select_operand(expr.then_expr,
                                                         type_hint)
            else:
                with self._forward_fill(expr, expr.then_expr):
                    then_type = self.analyze_expr(expr.then_expr)

            self.ctx.func.narrowed_types = dict(saved_narrowed)
            self.ctx.func.narrowed_types.update(else_facts)
            if type_hint is not None:
                else_type = self._analyze_select_operand(expr.else_expr,
                                                         type_hint)
            else:
                with self._forward_fill(expr, expr.else_expr):
                    else_type = self.analyze_expr(expr.else_expr)
        finally:
            self.ctx.cond_operand_depth -= 1

        self.ctx.func.narrowed_types = saved_narrowed

        # Strip Ref/Own from branch types -- these are provenance qualifiers,
        # not part of the result type.  The ternary produces a value.
        then_type = unwrap_own(unwrap_ref_type(then_type))
        else_type = unwrap_own(unwrap_ref_type(else_type))

        verdict = self._select_join(then_type, else_type,
                                    expr.then_expr, expr.else_expr)
        common = verdict.joined
        # A truth test takes each arm on its own, so arms with no one value
        # type (an int and a float, or a truth-tested and/or beside a float)
        # leave the ternary a bool.
        truth_test = expr in self.ctx.truth_test_selects
        if verdict.outcome is JoinOutcome.INT_FLOAT_MIX:
            slot_float = declared_float_slot(
                type_hint.type if type_hint else None)
            if (slot_float is not None
                    and self._slot_converts(verdict, type_hint)):
                common = slot_float
            elif truth_test:
                self.ctx.truth_tested_arms.add(expr)
                return BOOL
            else:
                raise self.ctx.error(select_mix_message(
                    verdict, "conditional expression",
                    self._ternary_rewrite(expr), expr.then_expr,
                    expr.else_expr,
                    self._mix_operand_types(then_type, else_type),
                    expr is self.ctx.name_initializer), expr)
        if common is None:
            t = self._select_default_type(then_type, expr.then_expr)
            e = self._select_default_type(else_type, expr.else_expr)
            if isinstance(e, NoneType):
                common = make_union(t, NoneType())
            elif isinstance(t, NoneType):
                common = make_union(e, NoneType())
            elif truth_test:
                self.ctx.truth_tested_arms.add(expr)
                return BOOL
            else:
                ts, es = self._select_types_for_message(then_type, else_type)
                raise self.ctx.error(
                    f"Incompatible types in ternary expression: "
                    f"'{ts}' and '{es}'", expr)
        self._commit_select(expr, common,
                            (("then_expr", then_type), ("else_expr", else_type)),
                            "ternary branch")
        return common

    def _mix_operand_types(self, a: TpyType,
                           b: TpyType) -> tuple[TpyType, TpyType]:
        """Select operand types as a mix diagnostic spells them: a pending
        container literal as the container it is."""
        return (self._normalize_pending_container(a),
                self._normalize_pending_container(b))

    def _name_target_annotation(
            self, expr: TpyExpr, annotation: Callable[[str], str],
    ) -> Callable[[str], str] | None:
        """`annotation` when `expr` is the whole value of an unannotated
        first binding, the one position a declared type can be offered for."""
        return annotation if expr is self.ctx.name_initializer else None

    @staticmethod
    def _ternary_rewrite(
            expr: TpyIfExpr) -> Callable[[str, str], str] | None:
        cond = operand_spelling(expr.condition)
        if cond is None:
            return None
        return lambda then_s, else_s: f"{then_s} if {cond} else {else_s}"

    def _resolve_literals_with_hint(self, t: TpyType,
                                    slot: SlotHint | None) -> TpyType:
        """Resolve IntLiteralType / FloatLiteralType and pending container types
        inside t using hint as a structural guide. Recurses into TupleType.
        Falls back to default_int / FLOAT when hint doesn't match the literal's
        family, and leaves a pending container pending when it doesn't.

        Mirrors the asymmetric behavior of the bare-literal path: integer
        literals adopt the hint when present (compat-checked downstream),
        float literals only adopt the hint when it's a float type.
        """
        hint = slot.type if slot is not None else None
        # An inferred hint leaves an int an int (`_hint_converts_int`).
        if isinstance(t, IntLiteralType):
            return (hint if hint is not None
                    and not self._hint_converts_int(t, slot)
                    else self.ctx.default_int_for_literal(t))
        if isinstance(t, FloatLiteralType):
            return hint if is_float_type(hint) else FLOAT
        if (hint is not None and self._pending_matches_hint(t, hint)
                and not self._hint_converts_int(t, slot)):
            # The enclosing slot's annotation is the authority for a nested
            # literal's type, one level at a time: the literal's own elements
            # were already checked against the hint's when it was analyzed.
            return hint
        if isinstance(t, TupleType):
            if isinstance(hint, TupleType) and len(t.element_types) == len(hint.element_types):
                elems = tuple(
                    self._resolve_literals_with_hint(
                        et, slot.map(element(k)))
                    for k, et in enumerate(t.element_types)
                )
            else:
                elems = tuple(
                    self._resolve_literals_with_hint(et, None)
                    for et in t.element_types
                )
            return TupleType(elems)
        return t

    def _pending_matches_hint(self, t: TpyType, hint: TpyType) -> bool:
        """Whether an unresolved container literal `t` is the annotated `hint`
        spelled as a literal: the same container category, or a fixed-size list
        literal at an `Array` slot (the one kind mismatch a list literal is
        allowed to resolve to).

        Keyed on the TypeDef category rather than on the pending class, so a
        pending kind and its concrete sibling are matched by the one fact that
        distinguishes containers."""
        if not isinstance(t, PENDING_CONTAINER_TYPES):
            return False
        if not isinstance(hint, NominalType):
            return False
        if isinstance(t, PendingListType) and is_array(hint):
            return self.type_ops.pending_list_matches_array(t, hint)
        td_t = type_def_of(t)
        td_h = type_def_of(hint)
        return td_t is not None and td_h is not None and td_t.category is td_h.category

    def _unify_literal_types(self, a: TpyType, b: TpyType) -> TpyType | None:
        """Unify two literal/pending element types (int/float literals, tuples,
        nested pending containers) for a homogeneous-container peer-unify. Thin
        wrapper over the shared `typesys.unify_literal_types` so the peer-unify
        and assignability paths share one definition and can't drift.

        Passes the demotion hook so a jagged pending list converges to vector on
        the same traversal -- wherever it sits: bare, or nested in a tuple / dict
        value (the sibling literals must share one C++ element type)."""
        return unify_literal_types(a, b, on_pending_pair=self.compat._demote_pending_pair)

    def _analyze_dict_literal(
        self, expr: TpyDictLiteral,
        key_hint: SlotHint | None = None,
        value_hint: SlotHint | None = None,
    ) -> TpyType:
        """Analyze a dict literal {key: value, ...}"""
        expected_key = key_hint.type if key_hint is not None else None
        expected_value = value_hint.type if value_hint is not None else None
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

        key_types = [self._analyze_and_strip(k, key_hint) for k in expr.keys]
        value_types = [self._analyze_and_strip(v, value_hint)
                       for v in expr.values]
        # A key or value whose int the hint would take as its float keeps
        # its own type: the literal is then joined like an unhinted one.
        if self._converting_element(key_types, key_hint) is not None:
            key_hint = expected_key = None
        value_slot = value_hint
        if self._converting_element(value_types, value_hint) is not None:
            value_hint = expected_value = None

        self._materialize_fresh_value_elements(
            expr.keys, key_types, expected_key, "dict literal key")
        self._materialize_fresh_value_elements(
            expr.values, value_types, expected_value, "dict literal value")

        # Unify key types
        if is_union_or_optional_type(expected_key) or isinstance(expected_key, AnyType):
            key_type = expected_key
            for i, kt in enumerate(key_types, 1):
                if kt == expected_key:
                    continue
                try:
                    self.compat.check_type_compatible(
                        kt, expected_key, f"dict literal key {i}", expr.loc,
                        source_expr=expr.keys[i - 1],
                        target_is_storage_form=True)
                except SemanticError:
                    exp_s, act_s = disambiguated_pair(expected_key, kt)
                    raise self.ctx.error(
                        f"Dict literal key {i} has type {act_s}, "
                        f"incompatible with annotated key type {exp_s}", expr)
        else:
            key_type = key_types[0]
            for i, kt in enumerate(key_types[1:], 2):
                verdict = join_inferred_value_types(
                    key_type, kt, self._unify_literal_types)
                if verdict.outcome is JoinOutcome.INT_FLOAT_MIX:
                    raise self.ctx.error(literal_mix_message(
                        verdict, "Dict has mixed key types", "key", i, kt,
                        key_type, expr.keys[:i], self._name_target_annotation(
                            expr, lambda f: f"dict[{f}, ...]")), expr)
                if verdict.joined is None:
                    raise self.ctx.error(
                        f"Dict has mixed key types: key {i} is {self._user_type_name(kt)}, "
                        f"but earlier keys are {self._user_type_name(key_type)}", expr,
                    )
                key_type = verdict.joined

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
                        source_expr=expr.values[i - 1],
                        target_is_storage_form=True)
                except SemanticError:
                    exp_s, act_s = disambiguated_pair(expected_value, vt)
                    raise self.ctx.error(
                        f"Dict literal value {i} has type {act_s}, "
                        f"incompatible with annotated value type {exp_s}", expr)
        else:
            value_type = value_types[0]
            for i, vt in enumerate(value_types[1:], 2):
                # Literal-aware unification; the demotion hook converges jagged
                # pending-list values -- bare or wrapped in a tuple/dict -- to one
                # C++ type (see the array-literal peer-unify).
                verdict = join_inferred_value_types(
                    value_type, vt, self._unify_literal_types)
                if verdict.outcome is JoinOutcome.JOINED:
                    value_type = verdict.joined
                    continue
                if verdict.outcome is JoinOutcome.INT_FLOAT_MIX:
                    if self._hint_gave_float(verdict, expr.values[:i],
                                             value_slot):
                        value_type = (value_type if verdict.int_first
                                      else vt)
                        continue
                    raise self.ctx.error(literal_mix_message(
                        verdict, "Dict has mixed value types", "value", i,
                        vt, value_type, expr.values[:i],
                        self._name_target_annotation(
                            expr, lambda f:
                            f"dict[{python_type_name(key_type)}, {f}]")),
                        expr)
                raise self.ctx.error(
                    f"Dict has mixed value types: value {i} is {self._user_type_name(vt)}, "
                    f"but earlier values are {self._user_type_name(value_type)}", expr,
                )

        if expr.loc is not None:
            if not isinstance(expected_key, AnyType):
                for k, kt in zip(expr.keys, key_types):
                    self._warn_storage_element_copy(k, kt)
            if not isinstance(expected_value, AnyType):
                for v, vt in zip(expr.values, value_types):
                    self._warn_storage_element_copy(v, vt)

        # Resolve any literal types (bare or nested in tuples) using the
        # annotation as a structural hint.
        key_type = self._resolve_literals_with_hint(key_type, key_hint)
        value_type = self._resolve_literals_with_hint(value_type, value_hint)
        # Container elements must be owned -- views can't be stored in a dict.
        if isinstance(key_type, PendingViewType):
            key_type = key_type.family.owned_type
        if isinstance(value_type, PendingViewType):
            value_type = value_type.family.owned_type

        self.type_ops.validate_hashable_container_elem(key_type, "dict key", expr.loc)
        return make_dict(key_type, value_type)

    def _analyze_set_literal(
        self, expr: TpySetLiteral,
        elem_hint: SlotHint | None = None,
    ) -> TpyType:
        """Analyze a set literal {value, ...}"""
        if not expr.elements:
            raise self.ctx.error(
                "Empty set literal requires type annotation "
                "(e.g. s: set[int] = set())", expr,
            )

        elem_types = [self._analyze_and_strip(e, elem_hint)
                      for e in expr.elements]
        expected_elem = elem_hint.type if elem_hint is not None else None
        # As for a dict literal: an element the hint would convert keeps
        # its own type.
        if self._converting_element(elem_types, elem_hint) is not None:
            elem_hint = expected_elem = None

        self._materialize_fresh_value_elements(
            expr.elements, elem_types, expected_elem, "set literal element")

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
                        source_expr=expr.elements[i - 1],
                        target_is_storage_form=True)
                except SemanticError:
                    exp_s, act_s = disambiguated_pair(expected_elem, et)
                    raise self.ctx.error(
                        f"Set literal element {i} has type {act_s}, "
                        f"incompatible with annotated element type {exp_s}", expr,
                    )
        else:
            elem_type = elem_types[0]
            for i, et in enumerate(elem_types[1:], 2):
                verdict = join_inferred_value_types(
                    elem_type, et, self._unify_literal_types)
                if verdict.outcome is JoinOutcome.INT_FLOAT_MIX:
                    raise self.ctx.error(literal_mix_message(
                        verdict, "Set has mixed element types", "element", i,
                        et, elem_type, expr.elements[:i],
                        self._name_target_annotation(
                            expr, lambda f: f"set[{f}]")), expr)
                if verdict.joined is None:
                    raise self.ctx.error(
                        f"Set has mixed element types: element {i} is {self._user_type_name(et)}, "
                        f"but earlier elements are {self._user_type_name(elem_type)}", expr,
                    )
                elem_type = verdict.joined

        if expr.loc is not None and not isinstance(expected_elem, AnyType):
            for elem, et in zip(expr.elements, elem_types):
                self._warn_storage_element_copy(elem, et)

        elem_type = self._resolve_literals_with_hint(elem_type, elem_hint)
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
                exp_s, act_s = disambiguated_pair(first_type, elem_type)
                raise self.ctx.error(f"List repetition element {i} has type {act_s}, expected {exp_s}", expr)

        # Repetition copies the element into every slot (CPython aliases),
        # so a C++-copy-deleted element (@nocopy or __del__, directly or via
        # fields/parents; __copy__ restores copyability) cannot back any
        # repeat container; reject with guidance before C++ template errors.
        for elem_type in elem_types:
            if self.ctx.is_type_non_copyable(elem_type):
                raise self.ctx.error(
                    f"Cannot repeat an element of non-copyable type "
                    f"{elem_type}: repetition copies the element into every "
                    f"slot. Construct each slot instead, e.g. "
                    f"[... for _ in range(n)]",
                    expr)

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
        self, expr: TpyListComprehension, expected_elem: SlotHint | None = None
    ) -> TpyType:
        return self._analyze_elem_comprehension(expr, expected_elem, kind="list")

    def _analyze_set_comprehension(
        self, expr: TpySetComprehension, expected_elem: SlotHint | None = None
    ) -> TpyType:
        return self._analyze_elem_comprehension(expr, expected_elem, kind="set")

    def _analyze_generator_expression(self, expr: TpyGeneratorExpression) -> TpyType:
        gen = expr.generator
        elem_type = self._resolve_comp_iterable(gen, expr)
        # Stamped where the comprehensions stamp it: lowering reads the fact
        # off the head (an `Iterator[Own[T]]` source has no genexpr lowering
        # yet).
        gen.owns_elements = isinstance(elem_type, OwnType)
        return self._analyze_genexpr_function(expr, elem_type)

    def _analyze_genexpr_function(self, expr: TpyGeneratorExpression,
                                  elem_type: TpyType) -> TpyType:
        """A genexpr is a generator function created where it is written.

        Its source is the first param, already analyzed above in this
        function's flow (it is evaluated at creation); every enclosing local
        the element and filters read is a further param, taken by reference.
        The body is analyzed once, as a function, so every per-function fact
        the frame emitter reads comes from the ordinary lifecycle."""
        gen = expr.generator
        outer = self.ctx.func.current_function
        loc = expr.loc
        inner: list[TpyStmt] = [TpyYield(expr.element_expr, loc=expr.element_expr.loc or loc)]
        for cond in reversed(gen.conditions):
            inner = [TpyIf(cond, inner, [], loc=cond.loc or loc)]
        # A `range(...)` source: its bounds are evaluated here, where the
        # genexpr is written, and handed over by value; the body loops over a
        # range of them, which the frame walks with plain counters instead of
        # an iterator over a Range object.
        # The function's own names (source, range bounds, unpack holder) must
        # not shadow a name the body reads from the enclosing function.
        body_names = collect_name_refs(expr.element_expr)
        for cond in gen.conditions:
            body_names |= collect_name_refs(cond)
        body_names |= {n for n in (gen.unpack_vars or [gen.var]) if n is not None}

        def fresh(base: str) -> str:
            # A frame's constructor spells a param `<name>_`, so a name one
            # underscore away from a body name collides there too.
            name, n = base, 0
            while any(name in (other, other + "_") or name + "_" == other
                      for other in body_names):
                n += 1
                name = f"{base}{n}"
            return name
        range_args: list[TpyExpr] = []
        it = gen.iterable
        if (isinstance(it, TpyCall) and it.func_name == "range" and not it.kwargs
                and 1 <= len(it.args) <= 3
                and not any(isinstance(a, TpyStarUnpack) for a in it.args)):
            range_args = list(it.args)
        src_name = fresh("__src")
        bound_names = [fresh(f"__r{i}") for i in range(len(range_args))]
        src: TpyExpr = TpyName(src_name, loc=loc)
        if range_args:
            src = TpyCall(TpyName("range", loc=loc),
                          [TpyName(n, loc=loc) for n in bound_names], loc=loc)
        if gen.unpack_vars is not None:
            synth = fresh("__for_tup_gx")
            unpack = TpyTupleUnpack(targets=list(gen.unpack_vars),
                                    value=TpyName(synth, loc=loc), loc=loc)
            loop = TpyForEach(synth, src, [unpack] + inner, loc=loc, is_tuple_unpack=True)
        else:
            loop = TpyForEach(gen.var, src, inner, loc=loc)
        self.ctx.genexpr_counter += 1
        if not isinstance(outer, TpyFunction):
            outer = None    # module level: the init sentinel
        owner = outer.name if outer is not None else "module"
        # A loop var binds a copy of a value element, so a `readonly` on one
        # (a view of a readonly dict) says nothing about it -- the for
        # statement peels it the same way.
        src_elem = peel_value_readonly(
            resolve_int_literals(elem_type, self.ctx.default_int_for_literal))
        if isinstance(src_elem, TupleType):
            src_elem = TupleType(tuple(
                peel_value_readonly(t) for t in src_elem.element_types))
        src_type: TpyType = NominalType("Iterable", (src_elem,), is_protocol=True,
                                        _module_qname=qnames.ITERABLE)
        # A borrowed container keeps its own type: the frame walks it by
        # begin/end, and a reference element (or a tuple member) aliases the
        # source instead of riding a copied step result.
        it_type = self.ctx.get_expr_type(gen.iterable)
        container_lvalue = (
            is_stable_address_lvalue(gen.iterable) and it_type is not None
            and not is_protocol_type(unwrap_readonly(unwrap_ref_type(it_type)))
            and builtin_modules.is_native_iterable(
                unwrap_readonly(unwrap_ref_type(it_type)),
                registry=self.ctx.registry))
        # A nested def cannot hand an unsettled literal type of ITS enclosing
        # function any further up, so there the deduced source slot stands in
        # for the container's own type.
        by_protocol = (container_lvalue and self.ctx.func.in_nested_def
                       and contains_pending_leaf(it_type))
        # The ELEMENT has no stand-in: the loop var is a frame field and needs
        # its type when the nested def ends, before the literal settles
        # (BUGS.md#genexpr-nested-def-literal-record-unpack).
        if self.ctx.func.in_nested_def and _has_pending_literal_below(
                elem_type, self.ctx.func.nested_def_first_literal):
            raise self.ctx.error(
                "a generator expression in a nested function cannot iterate a "
                "container whose elements are container literals of the "
                "enclosing function with no declared type; annotate the "
                "container where it is created", gen.iterable)
        if container_lvalue and not by_protocol:
            src_type = unwrap_ref_type(it_type)
        source_params: list[tuple[str, TpyType]] = [(src_name, src_type)]
        if range_args:
            source_params = [
                (n, resolve_int_literals(
                    unwrap_ref_type(self.ctx.get_expr_type(a) or elem_type),
                    self.ctx.default_int_for_literal))
                for n, a in zip(bound_names, range_args)]
        func = TpyFunction(
            name=f"{GENEXPR_FUNC_PREFIX}{owner}_{self.ctx.genexpr_counter}",
            params=source_params, return_type=None, body=[loop],
            is_generator=True, is_genexpr=True, loc=loc)
        func.genexpr_container_by_protocol = by_protocol
        func.genexpr_owner = owner if outer is not None else None
        self.ctx.genexpr_roots[func] = self.ctx.func.body_root
        # Every type param in scope where the expression is written is one of
        # the function's own: the enclosing record's, then the enclosing
        # function's.
        rec = self.ctx.record_ctx
        if rec is not None and rec.type_params:
            func.type_params = list(rec.type_params)
            func.type_param_kinds = list(rec.type_param_kinds or [])
            func.type_param_bounds = dict(rec.type_param_bounds or {})
        if outer is not None:
            func.type_params = func.type_params + list(outer.type_params)
            func.type_param_kinds = func.type_param_kinds + list(outer.type_param_kinds)
            func.type_param_bounds = {**func.type_param_bounds, **outer.type_param_bounds}
        # Inside a nested def the names it captured from ITS enclosing function
        # are capturable too: the frame takes a reference either way.
        assigned = (self.ctx.func.definitely_assigned
                    | (self.ctx.func.outer_scope_locals or set()))
        free_names = _nested_def_free_names(func)
        captures = [n for n in sorted(free_names) if n in assigned]
        reads = tuple(TpyName(name, loc=loc) for name in captures)
        narrowed: list[str] = []
        rebindable = [n for n in captures if self._capture_may_be_rebound(n)]
        for read in reads:
            # A capture borrows the enclosing variable whatever that variable
            # owns, so the param never takes its `Own`.
            captured = unwrap_own(self.analyze_expr(read))
            declared = (self.ctx.func.current_scope.lookup(read.name)
                        if self.ctx.func.current_scope is not None else None)
            bare = (unwrap_readonly(unwrap_ref_type(declared))
                    if declared is not None else None)
            is_narrowed = (isinstance(bare, (OptionalType, UnionType))
                           and captured != bare)
            if is_narrowed and read.name not in rebindable:
                narrowed.append(read.name)
            if is_narrowed and read.name in rebindable:
                # The body runs at each PULL, and something between two pulls
                # can rebind this name: the narrowing proved at creation does
                # not hold there, so the body sees the declared type and
                # checks its own reads.
                if not isinstance(bare, OptionalType):
                    raise self.ctx.error(
                        f"'{read.name}' is narrowed here, but the loop this "
                        f"generator expression feeds rebinds it between pulls; "
                        f"bind the narrowed value to a local first", read)
                captured = unwrap_ref_type(declared)
                self.ctx.set_expr_type(read, captured)
            func.params.append((read.name, captured))
        func.capture_params = tuple(captures)
        for name in captures:
            self.ctx.func.capture_sites.setdefault(
                name, (loc.line if loc else None, "a generator expression"))
        # The frame iterates the source through its own param while the body
        # may name the same container itself -- a capture, or a module global
        # it reads directly; the loop's loan on the param alone would not see
        # a growth through that name. A name free in the body resolves there
        # to the same storage it does here.
        source_loans = () if range_args else tuple(
            (key, loan)
            for key, loan in iterated_storage(self.ctx, gen.iterable).loans
            if _storage_root(key) in free_names)
        assert self.ctx.analyze_genexpr_function is not None
        self.ctx.analyze_genexpr_function(func, source_loans)
        assert func.generator_yield_type is not None
        # Creating the frame hands the source and the captures to the function
        # the way a call hands over its arguments, so its mutation facts reach
        # this function the same way: a body that mutates through its loop var
        # or a capture keeps the enclosing param a mutable borrow, and one that
        # may grow or rewrite a capture conflicts with the loans held on it here
        # and demotes the views borrowed out of it.
        fis = self.ctx.registry.get_function(func.name)
        if fis:
            creation = TpyCall(TpyName(func.name, loc=loc),
                               (range_args or [gen.iterable]) + list(reads), loc=loc)
            creation.resolved_function_info = fis[-1]
            # The frame iterates only its source and reads a capture afresh at
            # each pull, so a capture is held whole -- unless the yield may
            # point into it (`ys[i][1:]` views an element of `ys`).
            lent = ({r.name for lo in lent_operands(
                         expr.element_expr, func.generator_yield_type,
                         expr_type=None)
                     for r in lend_roots(self.ctx, lo.expr)}
                    if frame_yield_may_borrow(func.generator_yield_type)
                    else set())
            n_source = len(range_args) or 1
            fis[-1].root.held_whole_params = frozenset(
                n_source + k for k, name in enumerate(captures)
                if name not in lent)
            self.calls._check_borrow_arg_conflicts(creation)
            self.calls._check_loop_var_arg_mutation(creation)
            self.calls._record_mutation_call_edges(creation)
            expr.frame_creation = creation
        expr.frame_func = func
        expr.frame_captures = reads
        expr.frame_rebindable = tuple(rebindable)
        self.ctx.stmt_genexprs.append((expr, tuple(narrowed)))
        expr.frame_range_args = tuple(range_args)
        expr.result_elem_type = func.generator_yield_type
        return GenExprType(func.generator_yield_type)

    def _capture_may_be_rebound(self, name: str) -> bool:
        """Whether something that runs between two pulls of a genexpr created
        here can rebind `name`: the body of a `for` it feeds, or a closure
        that writes the name. An argument-position consumer (`sum`, `any`,
        `list`) pulls to the end inside one expression, so nothing can."""
        if name in self.ctx.func.closure_written_names:
            return True
        return any(name in _rebound_in(body) for body in self.ctx.for_head_bodies)

    def _track_pending_elem_field(self, node: object, attr: str, typ: TpyType) -> None:
        """Record a comprehension element/key/value-type snapshot for finalization.

        The snapshot is taken before the deferred resolver runs; if it holds a
        Pending* container type (a list-literal element), the resolver only
        updates the element node, leaving this cached copy stale and crashing
        codegen's to_cpp(). resolve_all replays these to read the resolved type.
        """
        if contains_pending_leaf(typ):
            self.ctx.func.pending_elem_type_fields.append((node, attr))

    def _analyze_elem_comprehension(
        self, expr: TpyListComprehension | TpySetComprehension,
        expected: SlotHint | None,
        kind: Literal["list", "set"],
    ) -> TpyType:
        """Shared analysis for list and set comprehensions."""
        gen = expr.generator
        elem_type = self._resolve_comp_iterable(gen, expr)
        # An Own[T]-yielding source (e.g. a generator) hands ownership to the
        # comprehension: a last-use bare-loop-var element is MOVED into the
        # result (codegen mirrors the consuming for-append), not copied.
        gen.owns_elements = isinstance(elem_type, OwnType)

        if self.scopes is None:
            raise RuntimeError(f"{kind} comprehension requires ScopeTracker")
        result_elem_type = self._enter_comp_scope(gen, expr, elem_type, expected)
        expected_elem = expected.type if expected is not None else None
        if self._converting_element([result_elem_type], expected) is not None:
            expected_elem = None

        if isinstance(result_elem_type, IntLiteralType):
            result_elem_type = expected_elem if expected_elem is not None else self.ctx.default_int_type
        if isinstance(result_elem_type, FloatLiteralType):
            result_elem_type = expected_elem if is_float_type(expected_elem) else FLOAT

        # Placed after the comp scope exits so the loop var is no longer in
        # `loop_vars`: the warning fires immediately rather than being deferred
        # for a consuming-iteration decision. Suppressed for an owned-source
        # bare-loop-var sink (the only shape codegen moves): the element
        # transfers ownership, so there is no copy to warn about. A derived sink
        # (`x.field`, `f(x)`) is not the move, so it still warns/copies.
        if (expr.element_expr.loc is not None and not isinstance(expected_elem, AnyType)
                and not self._comp_elem_moves(gen, expr.element_expr, is_last_sink=True)):
            self._warn_storage_element_copy(expr.element_expr, result_elem_type)

        if expected_elem is not None and result_elem_type != expected_elem:
            # Subclass coercion excluded: storing Child in list/set[Base] silently
            # slices objects (same invariance as container literals). Covariant-
            # generic wrapper upcasts (Box[Dog] -> Box[Pet]) are exempt -- a
            # representation-preserving converting move, not slicing -- and flow
            # through coerce_expr below.
            if (isinstance(result_elem_type, NominalType) and result_elem_type.is_user_record
                    and isinstance(expected_elem, NominalType) and expected_elem.is_user_record
                    and not self.compat.is_covariant_generic_upcast(result_elem_type, expected_elem)):
                exp_s, act_s = disambiguated_pair(expected_elem, result_elem_type)
                raise self.ctx.error(
                    f"{kind.capitalize()} comprehension element has type {act_s}, "
                    f"incompatible with annotated element type {exp_s}", expr
                )
            expr.element_expr = self.compat.coerce_expr(
                expr.element_expr, result_elem_type, expected_elem,
                f"{kind} comprehension element", coercion_ctx=CoercionContext.INIT,
                target_is_storage_form=True)
            result_elem_type = expected_elem

        if kind == "set":
            self.type_ops.validate_hashable_container_elem(result_elem_type, "set element", expr.loc)

        # Container elements are stored owned; collapse a per-element Own so the
        # node field matches the storage slot. Own[DynProtocol] would otherwise
        # render `unique_ptr<P>` here against a bare `P` slot. The unwrap only
        # peels the OUTER Own, so a tuple element additionally takes its owned
        # storage form -- otherwise its per-element markers survive and render as
        # reference members, and the comprehension aliases where the equivalent
        # literal copies.
        result_elem_type = owned_tuple_storage_type(unwrap_own(result_elem_type))
        expr.result_elem_type = result_elem_type
        self._track_pending_elem_field(expr, "result_elem_type", result_elem_type)

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

        # The Array build evaluates the element expression inside the
        # array_from_index lambda; an @error_return call's unwrap emits a
        # function-targeting `return`/`goto` (codegen's
        # _maybe_error_return_unwrap) that would mistarget from a lambda
        # body, so such elements stay on the inline vector path.
        if _expr_has_error_return_call(expr.element_expr):
            return None

        # range(N) or range(start, stop) with literal args
        if isinstance(gen.iterable, TpyCall) and gen.iterable.func_name == "range":
            size = self._range_literal_size(gen.iterable)
        else:
            # Array[T, N] source -- size is known from the type
            iterable_type = unwrap_readonly(self.ctx.get_expr_type(gen.iterable))
            size = iterable_type.type_args[1] if is_array(iterable_type) else None

        # array_from_index expands an N-element braced-init-list at C++
        # template-instantiation time; cap N so a huge literal range doesn't
        # become a compile-time / object-size cliff (the vector path is O(1)
        # emitted code regardless of N).
        if size is not None and size > _COMP_ARRAY_MAX_SIZE:
            return None
        return size

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
        key_hint: SlotHint | None = None,
        value_hint: SlotHint | None = None,
    ) -> TpyType:
        """Analyze a dict comprehension: {key: value for var in iterable if cond}"""
        gen = expr.generator
        elem_type = self._resolve_comp_iterable(gen, expr)
        gen.owns_elements = isinstance(elem_type, OwnType)

        if self.scopes is None:
            raise RuntimeError("dict comprehension requires ScopeTracker")
        key_type, value_type = self._enter_comp_scope(
            gen, expr, elem_type, (key_hint, value_hint))
        expected_key = key_hint.type if key_hint is not None else None
        expected_value = value_hint.type if value_hint is not None else None
        if self._converting_element([key_type], key_hint) is not None:
            expected_key = None
        if self._converting_element([value_type], value_hint) is not None:
            expected_value = None

        if isinstance(key_type, IntLiteralType):
            key_type = expected_key if expected_key is not None else self.ctx.default_int_type
        if isinstance(value_type, IntLiteralType):
            value_type = expected_value if expected_value is not None else self.ctx.default_int_type
        if isinstance(key_type, FloatLiteralType):
            key_type = expected_key if is_float_type(expected_key) else FLOAT
        if isinstance(value_type, FloatLiteralType):
            value_type = expected_value if is_float_type(expected_value) else FLOAT

        # See the list/set comprehension path for the loop-var-scope timing
        # rationale (placed after scope exit so the warning is not deferred).
        # Key and value are suppressed independently for an owned bare-loop-var
        # sink (the one shape codegen moves) -- `{node.id: node}` moves the value
        # but still copies/warns the key.
        if (expr.key_expr.loc is not None and not isinstance(expected_key, AnyType)
                and not self._comp_elem_moves(gen, expr.key_expr)):
            self._warn_storage_element_copy(expr.key_expr, key_type)
        if (expr.value_expr.loc is not None and not isinstance(expected_value, AnyType)
                and not self._comp_elem_moves(gen, expr.value_expr, is_last_sink=True)):
            self._warn_storage_element_copy(expr.value_expr, value_type)

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
        # A dict owns its values, so a tuple value takes the owned storage form
        # like every other container element. Left uncollapsed it keeps its
        # per-element markers and renders a reference member into the map value
        # (`ordered_map<K, std::tuple<A, B&>>`), which then ALIASES while the
        # store's copy warning says otherwise.
        value_type = owned_tuple_storage_type(value_type)
        expr.result_key_type = key_type
        expr.result_value_type = value_type
        self._track_pending_elem_field(expr, "result_key_type", key_type)
        self._track_pending_elem_field(expr, "result_value_type", value_type)
        return make_dict(key_type, value_type)

    def _resolve_comp_iterable(
        self, gen: TpyComprehensionGenerator, expr: TpyExpr
    ) -> TpyType:
        """Analyze the iterable and extract its element type (shared by all comprehensions)."""
        iterable_type = self.analyze_expr(gen.iterable)
        inner = unwrap_readonly(iterable_type)
        # The implicit `__iter__()` call, as the for statement records it;
        # before the comp scope, where a target named like the source's root
        # would shadow the enclosing binding.
        _record_iter_receiver_mutation(
            self.ctx, gen.iterable,
            unwrap_readonly(unwrap_own(unwrap_ref_type(iterable_type))))
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
            self._register_comp_iter_loans(gen, names)
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

        # An owned-yielding source binds the element non-const (`auto&&`) so a
        # last-use sink can move it -- mirrors the consuming for-loop, which
        # excludes consumed vars from the const-ref binding.
        if (worth_const_ref and not gen.owns_elements
                and not any(n in self.ctx.func.mutated_loop_vars for n in names)):
            gen.const_loop_var = True
        return result

    def _register_comp_iter_loans(self, gen: TpyComprehensionGenerator,
                                  names: list[str]) -> None:
        """The comprehension's iteration loan -- the one a `for` over the same
        source files, so its condition or element growing the source is
        diagnosed the same way.

        A source rooted at a name spelled like one of the targets
        (`[grow(c) for c in c]`) is the ENCLOSING binding of that name, which
        the comprehension's own code cannot reach by name, so no loan is filed
        on it: inside the scope that name is the target."""
        iterable_type = self.ctx.get_expr_type(gen.iterable)
        assert iterable_type is not None
        # `loan.unplaceable` needs no stamp here: a chain too deep for a loan
        # key never lowers as a comprehension source.
        register_iteration_loans(self.ctx, gen.iterable, iterable_type,
                                 excluded_roots=names)
        # The targets' element origin is not recorded here, so a view hoist
        # must not read the roots an earlier `for` over the same name left.
        for name in names:
            self.ctx.func.loop_var_iter_roots[name] = None

    def _analyze_comp_body(
        self,
        gen: TpyComprehensionGenerator,
        expr: TpyListComprehension | TpySetComprehension | TpyDictComprehension | TpyGeneratorExpression,
        hint: SlotHint | tuple[SlotHint | None, SlotHint | None] | None,
    ) -> TpyType | tuple[TpyType, TpyType]:
        for cond in gen.conditions:
            self.analyze_condition(cond)
        if isinstance(expr, TpyDictComprehension):
            key_hint, value_hint = hint
            return (self.analyze_expr_with_hint(expr.key_expr, key_hint),
                    self.analyze_expr_with_hint(expr.value_expr, value_hint))
        return self.analyze_expr_with_hint(expr.element_expr, hint)

    def _analyze_and_strip(self, expr: TpyExpr, hint: SlotHint | None = None) -> TpyType:
        """Analyze an expression and strip sema-internal wrappers (Own/Ref).

        Used for container literal element types where the declared type
        should be the user-facing type, not the provenance-tagged expression type.
        """
        self.analyze_expr_with_hint(expr, hint)
        result = self.ctx.get_expr_type(expr)
        assert result is not None
        return result

    def _analyze_tuple_literal(
        self, expr: TpyTupleLiteral,
        element_hints: list[SlotHint | None] | None = None
    ) -> TupleType:
        """Analyze a tuple literal (expr, expr, ...)."""
        elem_types = []
        for i, elem in enumerate(expr.elements):
            elem_slot = element_hints[i] if element_hints and i < len(element_hints) else None
            if elem_slot is not None:
                hint = elem_slot.type
                analyzed = self.analyze_expr_with_hint(elem, elem_slot)
                # Preserve Own[] from hint when the analyzed type matches
                if isinstance(hint, OwnType) and not isinstance(analyzed, OwnType):
                    analyzed = OwnType(analyzed)
                # Materialize a fresh-value element coercion (BigInt->fixed-int,
                # char->str) so it reaches codegen; other element coercions are
                # handled by the outer tuple compat + the tuple-literal slot
                # codegen, so this is a no-op for them.
                new_elem = self._materialize_narrowing_element_coercion(
                    elem, analyzed, hint, f"tuple element {i + 1}",
                    target_is_storage_form=False)
                expr.elements[i] = new_elem
                # A fired coercion retypes the element to the hint it produced.
                elem_types.append(hint if new_elem is not elem else analyzed)
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

    def check_dict_key(self, sub: TpySubscript, key_type: TpyType,
                       index_type: TpyType) -> None:
        """Validate a dict subscript's key against the dict's key type and
        store back the node the lookup must carry. The map lookup takes the
        key against `const K&` and converts nothing of its own, so a
        fresh-value rule dropped from the node never compiles (a `char` key at
        `dict[str, V]` reaches `erase`/`find` raw). Every dict subscript --
        read, store, `del` -- comes through here, so the key narrow has one
        owner and one spelling."""
        context = "dict key"
        coercion = self.compat.check_type_compatible(
            index_type, key_type, context, loc=sub.loc, source_expr=sub.index)
        sub.index = self.compat.materialize_fresh_value(
            sub.index, index_type, key_type, context, coercion,
            CoercionContext.ARG)

    def _analyze_subscript(self, expr: TpySubscript,
                          obj_type: TpyType | None = None) -> TpyType:
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

        if obj_type is None:
            obj_type = self.analyze_expr(expr.obj)

        # Unwrap transparent wrappers -- Ref/Own don't affect subscript behavior
        inner_obj_type = unwrap_ref_type(obj_type)
        if isinstance(inner_obj_type, OwnType):
            inner_obj_type = inner_obj_type.wrapped

        # Tuple indexing: t[0], t[-1] -- compile-time constant index only
        actual_for_tuple = unwrap_readonly(inner_obj_type)
        if isinstance(actual_for_tuple, TupleType):
            elem = self._analyze_tuple_subscript(expr, actual_for_tuple)
            # Without projecting readonly onto the element, a write through
            # `t[i]` of a readonly tuple is silently accepted.
            if isinstance(inner_obj_type, ReadonlyType) and not elem.is_value_type():
                elem = ReadonlyType(unwrap_readonly(elem))
            return elem

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

        # Dict subscript: d[key] -> V (key can be non-integer). The key is only
        # looked up, so a readonly key (e.g. one bound from iterating a readonly
        # dict) is accepted -- unwrap it for the key-type compat. A read through
        # a readonly dict yields a readonly value (a non-value V must not be
        # mutated through the borrow), mirroring the readonly element reprojection
        # the int-indexed container path applies below.
        readonly_dict = isinstance(inner_obj_type, ReadonlyType)
        lookup_index_type = unwrap_readonly(index_type)
        if isinstance(actual_obj, PendingDictType):
            # An unresolved key type has nothing to check against; every other
            # pending dict takes the same narrow a resolved one does.
            if not isinstance(actual_obj.key_type, UnknownElementType):
                self.check_dict_key(expr, actual_obj.key_type,
                                    lookup_index_type)
            v_type = actual_obj.value_type
            if readonly_dict and not v_type.is_value_type():
                v_type = ReadonlyType(unwrap_readonly(v_type))
            return make_ref(v_type)
        if is_dict(actual_obj):
            k_type = actual_obj.type_args[0]
            v_type = actual_obj.type_args[1]
            self.check_dict_key(expr, k_type, lookup_index_type)
            if readonly_dict and not v_type.is_value_type():
                v_type = ReadonlyType(unwrap_readonly(v_type))
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
                self._credit_slice_getitem_call(expr, actual_type, fi)
                return ret
            raise self.ctx.error(f"Slicing is not supported for {inner_obj_type}", expr)

        # User-defined mapping with a NON-integer key (e.g. Counter over
        # dict[T,int]): dispatch to its single-key __getitem__ before the
        # sequence integer-index gate, validating the index against the key.
        # Int indices keep their existing path below (record __getitem__ is
        # handled there), so only non-int keys are intercepted here.
        if not is_any_int_type(index_type):
            ro_obj = isinstance(inner_obj_type, ReadonlyType)
            bare_obj = unwrap_readonly(inner_obj_type)
            if isinstance(bare_obj, NominalType) and bare_obj.is_record:
                kr = self.narrowing.record_getitem_key_ret(bare_obj)
                if kr is not None:
                    key_t, ret_t = kr
                    if self.compat.is_type_compatible(unwrap_readonly(index_type), unwrap_readonly(key_t)):
                        self._tag_record_getitem(expr, bare_obj, ret_t, index_type)
                        if ro_obj and not ret_t.is_value_type():
                            ret_t = _readonly_result(ret_t)
                        return make_ref(ret_t)

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
            # A protocol method has no FunctionInfo: the credit is eager.
            if (not expr.is_write_target
                    and not _protocol_getitem_is_readonly(actual_type)):
                credit_implicit_receiver_call(
                    self.ctx, expr.obj, actual_type, None, "__getitem__", expr)
            if is_readonly_obj and not ret.is_value_type():
                ret = ReadonlyType(unwrap_readonly(ret))
            return make_ref(ret)

        # Records with __getitem__ method
        if isinstance(actual_type, NominalType) and actual_type.is_record:
            ret = self.narrowing._get_record_getitem_type(actual_type)
            if ret is None:
                raise self.ctx.error(f"Cannot index type {actual_type}: no __getitem__ method", expr)
            self._tag_record_getitem(expr, actual_type, ret, index_type)
            if is_readonly_obj and not ret.is_value_type():
                ret = _readonly_result(ret)
            return make_ref(ret)

        raise self.ctx.error(f"Cannot index type {obj_type}", expr)

    def _tag_record_getitem(self, expr: TpySubscript, record_type: NominalType,
                            ret_type: TpyType, index_type: TpyType) -> None:
        """Record the resolved user-record __getitem__ on the subscript node
        (substitution-composed, like a method call's resolved callee), so the
        borrow/storage and value-category classifiers can treat the read as a
        method CALL -- a pointer-repr Optional return is borrow-form T*, not a
        storage `std::optional<T>` element lvalue, and a by-VALUE return is a
        temporary where a container element read is an lvalue.

        Tagged exactly when the accessor's COMPOSED return is not a C++
        reference -- the shapes whose consumption differs from a container
        element read. A reference-returning accessor consumes identically, so
        it stays untagged and its reads keep the container spelling (the
        re-resolved signature below is unsubstituted, so a generic's `-> T`
        cannot answer the question anyway).

        Every record subscript is also an `obj.__getitem__(k)` call on its
        receiver, credited here whatever the return shape."""
        self._credit_record_getitem_call(expr, record_type, index_type)
        actual_ret = unwrap_readonly(ret_type)
        if return_type_is_cpp_ref(actual_ret):
            return
        record = self.ctx.registry.get_record_for_type(record_type)
        if record is None:
            return
        # The accessor hands back a borrow (`T*`) into the receiver, valid only
        # while the receiver lives. An rvalue receiver (a temporary) dies at the
        # end of the full-expression, so the borrow would dangle. A pointer-repr
        # Optional is always a reference type, which TPy never silently copies --
        # there is no valid non-copying result to hand back. Reject loudly until
        # the general borrow/liveness pass (BUGS.md rvalue-subscript entry)
        # replaces this with scope-based reasoning.
        if (isinstance(actual_ret, OptionalType)
                and actual_ret.uses_pointer_repr()
                and is_rvalue_source(self.ctx, expr.obj)):
            raise self.ctx.error(
                "subscript into a temporary would dangle: the accessor returns "
                "a borrow into the receiver, which is freed at the end of this "
                "expression -- bind the receiver to a local first", expr)
        # Re-resolved UNSUBSTITUTED: for a generic record this carries the raw
        # `V | None` signature, not the composed `Rec | None`. Safe only because
        # the sole consumer (call_returns_cpp_ref) keys on the return's outer
        # shape; a consumer needing the substituted inner type must thread the
        # already-composed FunctionInfo from _get_record_getitem_type instead.
        expr.getitem_function_info = self.protocols.lookup_record_method(
            record, "__getitem__")

    def _credit_record_getitem_call(self, expr: TpySubscript,
                                    record_type: NominalType,
                                    index_type: TpyType) -> None:
        """Credit the single-key `__getitem__` a record subscript calls as a
        method call on the receiver (a slice overload is the slice path's).

        The overload is the one `resolve_overload` picks for `index_type`,
        as the `__contains__` arm resolves its own; when that fails or is
        ambiguous, any mutating overload counts. The subscript's TYPE still
        comes from the first overload (BUGS.md#record-getitem-overload-first-wins).
        """
        if expr.is_write_target:
            return
        record = self.ctx.registry.get_record_for_type(record_type)
        if record is None:
            return
        overloads, inherited_subst = self.protocols.lookup_record_method_overloads(
            record, "__getitem__")
        keyed = [fi for fi in overloads
                 if not _is_slice_getitem(fi) and len(fi.params) == 1]
        callee = self._resolve_getitem_overload(
            keyed, record_type, inherited_subst, unwrap_readonly(index_type))
        if callee is None:
            callee = next((fi for fi in keyed if call_mutates_receiver(fi)), None)
        if callee is not None:
            credit_implicit_receiver_call(
                self.ctx, expr.obj, record_type, callee, "__getitem__", expr)

    def _resolve_getitem_overload(
            self, keyed: list['FunctionInfo'], record_type: NominalType,
            inherited_subst: dict[str, TpyType | int],
            index_type: TpyType) -> 'FunctionInfo | None':
        """The single-key `__getitem__` overload `index_type` selects, or None.

        Substituted as an explicit call's receiver is (the inherited
        substitution composed with the instance's); a method-level type param
        stays unbound and matches structurally."""
        if len(keyed) < 2:
            return keyed[0] if keyed else None
        instance_subst = self.type_ops.build_type_substitution(record_type)
        composed = {
            k: (_substitute_type_params(v, {n: t for n, t in instance_subst.items()
                                            if isinstance(t, TpyType)})
                if isinstance(v, TpyType) else v)
            for k, v in inherited_subst.items()}
        subst = {k: v for k, v in {**instance_subst, **composed}.items()
                 if isinstance(v, TpyType)}
        subst_keyed = [
            dc_replace(m, params=[dc_replace(p, type=_substitute_type_params(p.type, subst))
                                  for p in m.params])
            for m in keyed] if subst else keyed
        # An int literal takes the first overload whose key it fits, as the
        # `__contains__` arm does.
        arg = index_type
        if isinstance(arg, IntLiteralType):
            for m in subst_keyed:
                tr = int_traits_of(m.params[0].type)
                if tr is not None and tr.min_value <= arg.value <= tr.max_value:
                    arg = m.params[0].type
                    break
            else:
                arg = self.ctx.default_int_for_literal(arg)
        try:
            matched = resolve_overload(subst_keyed, [arg])
        except OverloadAmbiguityError:
            return None
        return next((keyed[i] for i, m in enumerate(subst_keyed) if m is matched),
                    None)

    def _credit_slice_getitem_call(self, expr: TpySubscript, actual_type: TpyType,
                                   fi: 'FunctionInfo') -> None:
        """A user record's slice `__getitem__` is a call on the receiver too;
        a builtin's is the container's own, answered by its element rules."""
        if (not expr.is_write_target and isinstance(actual_type, NominalType)
                and actual_type.is_user_record):
            credit_implicit_receiver_call(
                self.ctx, expr.obj, actual_type, fi, "__getitem__", expr)

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
            self._credit_slice_getitem_call(expr, actual_type, fi)
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
                        "Format specs on int are not yet supported (use a fixed-width type like int32)",
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

    def _analyze_lambda_with_fn_hint(self, expr: TpyLambda,
                                     fn_hint: SlotHint | CallableType,
                                     *, as_class: str | None = None) -> CallableType:
        """Analyze a lambda with a Fn/Callable type hint providing parameter types.

        `as_class` names the class a synthesized factory lambda stands for,
        so its diagnostics speak of the class name the user wrote. Where the
        hint's return is inferred (`SlotHint`), the parameters still type the
        lambda's, but a body that returns an int where the hint returns a
        float keeps its int return.
        """
        fn_slot = SlotHint.of(fn_hint)
        fn_type = fn_slot.type
        if as_class is not None:
            origin = f"'{as_class}' used as a '{fn_type}'"
            try:
                return self._analyze_lambda_with_fn_hint(expr, fn_slot)
            except _LambdaResultMismatch as e:
                raise SemanticError(
                    f"{origin} constructs a '{e.body_type}', which is not "
                    f"compatible with its result type "
                    f"'{fn_type.return_type}'", e.loc) from None
            except SemanticError as e:
                raise SemanticError(f"{origin}: {e.message}", e.loc) from None
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

            # The body is the lambda's return value, so it takes the return
            # slot as its hint like a `return` statement does -- unless the
            # slot is still being inferred from the body.
            ret_hint = fn_type.return_type
            ret_slot = fn_slot.map(callable_return)
            if (ret_hint is None or isinstance(ret_hint, VoidType)
                    or contains_type_param(ret_hint)):
                body_type = self.analyze_expr(expr.body)
            else:
                body_type = self.analyze_expr_with_hint(expr.body, ret_slot)

        # Detect captures: names in body that are local variables from the outer scope
        # (not lambda params, not global functions, not builtins)
        param_set = set(expr.param_names)
        free_names = collect_name_refs(expr.body)
        captured = sorted((free_names - param_set) & outer_locals)
        expr.captured_names = captured
        for name in captured:
            self.ctx.func.capture_sites.setdefault(
                name, (expr.loc.line if expr.loc else None, "a lambda"))
        # Callable context: captures must be by value (std::function can escape).
        # Fn (template) stays inline; captures by reference are safe.
        if isinstance(fn_type, CallableType) and not fn_type.is_template:
            expr.captures_by_value = True
        # Send/Sync frame fact: classify the capture list so conversion
        # sites (Send[Callable[...]] slots) can consult the concrete frame.
        # A captured receiver is by-ref in every mode: codegen captures
        # `this` (an alias), never a copy -- must classify non-Send.
        by_ref = not expr.captures_by_value
        self_is_receiver = self.ctx.receiver_self_in_scope()
        expr.frame_type = build_closure_frame([
            (name, self._lookup_capture_type(name),
             by_ref or (name == "self" and self_is_receiver))
            for name in captured
        ])

        # Check return type compatibility (allow implicit coercions like int literal -> int32)
        if (isinstance(fn_type.return_type, TypeParamRef)
                or self._hint_converts_int(body_type, ret_slot)):
            # Hint has unresolved type param (e.g. from generic builtin map[T,U]):
            # use the body's inferred type and return a concrete CallableType.
            # An inferred hint's float return does not convert an int body
            # either: the lambda returns what its body does.
            # Resolve IntLiteralType so overload resolution sees a concrete int type.
            if isinstance(body_type, IntLiteralType):
                body_type = self.ctx.default_int_for_literal(body_type)
            elif not isinstance(fn_type.return_type, TypeParamRef):
                # A literal body takes the return hint where that converts
                # nothing, its default elsewhere; a pending literal returns
                # the container it resolves to.
                body_type = self._resolve_literal_type(self._select_default_type(
                    self._resolve_literals_with_hint(unwrap_own(body_type),
                                                     ret_slot)))
            expr.inferred_return_type = body_type
            self.compat.check_view_return_dangle(expr.body, body_type, expr.loc)
            self._queue_lambda_borrow_check(expr, body_type)
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
                err = self.ctx.error(
                    f"Lambda body type '{body_type}' is not compatible with "
                    f"expected return type '{fn_type.return_type}'",
                    expr)
                raise _LambdaResultMismatch(err.message, err.loc, body_type)
        self.compat.check_view_return_dangle(expr.body, fn_type.return_type, expr.loc)
        self._queue_lambda_borrow_check(expr, fn_type.return_type)
        if not isinstance(fn_type.return_type, VoidType):
            expr.body = self.compat.coerce_expr(
                expr.body, body_type, fn_type.return_type, "lambda return",
                coercion_ctx=CoercionContext.RETURN, is_return=True)

        expr.inferred_return_type = fn_type.return_type
        return fn_type

    def _queue_lambda_borrow_check(self, expr: TpyLambda,
                                   result: TpyType | None) -> None:
        if isinstance(result, RefType):
            self.ctx.pending_lambda_borrow_checks.append(expr)

    def resolve_pending_lambda_borrow_checks(self) -> None:
        """Reject a lambda whose borrow result points into a value its own
        body created: the value dies when the lambda returns, before the
        caller reads the borrow. Only a PROVEN temporary root rejects; the
        by-value result that would make the shape valid is
        BUGS.md#lambda-body-partial-return-checks. A lambda re-analyzed at a
        by-value result since it was queued is judged by its final result."""
        pending = self.ctx.pending_lambda_borrow_checks[:]
        self.ctx.pending_lambda_borrow_checks.clear()
        for lam in pending:
            result = lam.inferred_return_type
            if not isinstance(result, RefType):
                continue
            root = self.compat.borrow_temp_root(lam.body)
            if root is None:
                continue
            what = _temp_root_spelling(root)
            into = f"'{what}', a value" if what else "a value"
            raise self.ctx.error(
                f"Cannot return a borrow of a temporary from this lambda: "
                f"its result points into {into} created in the lambda body, "
                f"which is destroyed when the lambda returns. Use a def "
                f"returning 'Own[{result.wrapped}]' instead: bind the value "
                f"to a local there and return tpy.copy(...) of the part you "
                f"need", lam)

    # --- Class names as callables ---

    def _names_class(self, name: str) -> bool:
        """Whether `name` is bound to a class a call constructs: a record,
        an enum, or an imported class with a constructor. A function,
        module, protocol, variable or type form (`Fn`, `Own`) is not."""
        ns = self.ctx.func.current_ns
        binding = ns.lookup(name) if ns else None
        if binding is None:
            return False
        if binding.kind == BindingKind.ENUM:
            return True
        record = self.ctx.class_record_of(binding)
        if binding.kind == BindingKind.RECORD:
            # `cls` in a classmethod is the class as a value, which needs
            # `type[T]`; its own diagnostic says so.
            return not binding.is_sema_alias
        if binding.kind != BindingKind.IMPORTED_NAME or not binding.import_source:
            return False
        if self.ctx.registry.get_function(name):
            return False
        if (self.calls._record_for_local_name(name) is not None
                or self.ctx.registry.get_enum(name) is not None):
            return True
        # Found only through its qualified import (a builtin): a class only
        # when it declares a constructor, which a type form does not.
        return (record is not None
                and bool(record.get_method_overloads("__init__")))

    def _try_class_factory(self, expr: TpyName,
                           hint: SlotHint) -> CallableType | None:
        """A class name at a Callable/Fn slot stands for `lambda *a: C(*a)`
        at the slot's arity, so the slot's parameter types pick the
        constructor overload and its return type types the construction.
        The lambda is kept on the name (`factory_expansion`) and lowered in
        its place. None when the name is not a class."""
        if not self._names_class(expr.name):
            return None
        lam = self._class_factory_lambda(expr, len(hint.type.param_types))
        typ = self._analyze_lambda_with_fn_hint(lam, hint, as_class=expr.name)
        self.ctx.set_expr_type(lam, typ)
        expr.factory_expansion = lam
        return typ

    @staticmethod
    def _class_factory_lambda(expr: TpyName, arity: int) -> TpyLambda:
        """`lambda *a: C(*a)` for the class name `expr`, unanalyzed."""
        pnames = [f"__tpy_fa{i}" for i in range(arity)]
        call = TpyCall(func=TpyName(expr.name, loc=expr.loc),
                       args=[TpyName(p, loc=expr.loc) for p in pnames],
                       loc=expr.loc)
        return TpyLambda(param_names=pnames, body=call, loc=expr.loc)

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
                        if expr.name in self.ctx.func.nested_def_names:
                            # Frame members have no value form -- reject any
                            # value use in a resumable body (Callable AND Fn).
                            self.ctx.reject_resumable_nested_def_escape(
                                expr.name, expr)
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
        for param_name, bound_arg in inferred.items():
            if param_name in fi.type_param_bounds:
                bound = fi.type_param_bounds[param_name]
                if not self.protocols.type_conforms_to_protocol(bound_arg, bound):
                    return None, (
                        f"Cannot use '{fi.name}' as function reference: "
                        f"inferred type argument {bound_arg} for {param_name} "
                        f"does not satisfy bound '{bound.name}'"
                    )
        # A still-OPEN type argument has no C++ parameter form at all, so the
        # Fn/Callable value cannot carry a signature.
        for param_name, bound_arg in inferred.items():
            if contains_type_param(bound_arg):
                return None, (
                    f"Cannot use '{fi.name}' as function reference with "
                    f"{param_name}={bound_arg}: the type argument is not "
                    f"resolved (use a lambda instead)"
                )
        return tuple(inferred[tp] for tp in fi.type_params), None
