"""
TurboPython Method Analysis

Method call and super() analysis.
"""

from __future__ import annotations
import copy
from typing import TYPE_CHECKING, Callable

from .. import qnames
from ..typesys import (
    TpyType, NominalType, OwnType, OptionalType, PendingListType, PendingDictType, PendingSetType,
    SuperType, TypeParamRef, FunctionInfo, ParamInfo, VOID, is_protocol_type,
    PtrType, ReadonlyType, unwrap_readonly, UnknownElementType,
    PendingGenericInstanceType, IntLiteralType, CallableType, unwrap_ref_type, unwrap_qualifiers, unwrap_send_sync, is_any_int_type,
    unwrap_own, ConcreteCoroType,
    RecordInfo,
    contains_type_param,
    FloatLiteralType, resolve_int_literals,
)
from ..parse import (
    TpyCall, TpyMethodCall, TpyName, TpyFieldAccess, TpyFunction, TpyExprStmt, TpyStrLiteral, TpyStmt,
    TpyStarUnpack,
    is_docstring,
    TpyFString, TpyExpr, TpyCoerce, TpySubscript,
    is_super_del_call,
)
from ..namespace import BindingKind
from ..coercions import CoercionContext
from ..prescan import _expr_to_narrowing_key
from .narrowing import deref_view_narrowed
from ..diagnostics import OPTIONAL_NONE_ACCESS_WARNING, SemanticError
from ..type_def_registry import is_list, is_set, is_fstr_type, is_borrowing_view_type, protocol_info_of
from .overloads import resolve_overload, OverloadAmbiguityError
from .bound_check import raise_if_class_param_bound_violated
from .calls import (
    arity_error_msg, resolve_kwargs, validate_generic_defaults,
    validate_type_param_bounds,
    _enrich_literal_types,
    resolve_inferred_type_arg,
)
from .type_ops import ReturnSeed, seeded_arg_hint
from .context import PENDING_CONTAINER_TYPES
from .receiver_calls import (check_receiver_call_loans, credit_receiver_mutation,
                             receiver_is_readonly)

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker
    from .compatibility import TypeCompatibility
    from .local_deduction import LocalTypeDeduction
    from .expressions import ExpressionAnalyzer
    from .calls import CallAnalyzer
    from ..typesys import PendingGenericInstanceInfo

from .local_deduction import mark_pending_list_mutated, view_source_is_temporary


# Single-element container inserts and the arg index that lands in element
# storage -- used to reject a temporary rvalue stored into a view-typed element.
_VIEW_ELEM_INSERTS = {"append": 0, "add": 0, "insert": 1}


def _unresolved_params_in_type(typ: TpyType, inferred: dict[str, TpyType], param_names: set[str]) -> list[str]:
    """Return list of type param names that appear in typ but are not yet in inferred."""
    result: list[str] = []
    _collect_unresolved(typ, inferred, param_names, result)
    return result


def _collect_unresolved(
    typ: TpyType, inferred: dict[str, TpyType], param_names: set[str], out: list[str],
) -> None:
    if isinstance(typ, TypeParamRef):
        if typ.name in param_names and typ.name not in inferred and typ.name not in out:
            out.append(typ.name)
        return
    if isinstance(typ, NominalType) and typ.type_args:
        for a in typ.type_args:
            if isinstance(a, TpyType):
                _collect_unresolved(a, inferred, param_names, out)
        return
    for attr in ('element_type', 'pointee', 'inner', 'wrapped'):
        inner = getattr(typ, attr, None)
        if inner is not None and isinstance(inner, TpyType):
            _collect_unresolved(inner, inferred, param_names, out)
    if hasattr(typ, 'element_types'):
        for e in typ.element_types:
            _collect_unresolved(e, inferred, param_names, out)
    if hasattr(typ, 'key_type') and hasattr(typ, 'value_type'):
        _collect_unresolved(typ.key_type, inferred, param_names, out)
        _collect_unresolved(typ.value_type, inferred, param_names, out)
    if hasattr(typ, 'members'):
        for m in typ.members:
            _collect_unresolved(m, inferred, param_names, out)


def _resolve_dotted_record_chain(expr: TpyFieldAccess, ctx: 'SemanticContext') -> str | None:
    """Resolve a chain of field accesses to a nested record dotted name.

    Returns the dotted name (e.g., "Outer.Mid") if the chain resolves to a
    registered nested record, or None otherwise.
    """
    if isinstance(expr.obj, TpyName):
        if ctx.func.current_ns:
            binding = ctx.func.current_ns.lookup(expr.obj.name)
            if binding and binding.kind in (BindingKind.RECORD, BindingKind.IMPORTED_NAME):
                dotted = f"{expr.obj.name}.{expr.field}"
                if ctx.registry.get_record(dotted) is not None:
                    return dotted
    elif isinstance(expr.obj, TpyFieldAccess):
        parent = _resolve_dotted_record_chain(expr.obj, ctx)
        if parent is not None:
            dotted = f"{parent}.{expr.field}"
            if ctx.registry.get_record(dotted) is not None:
                return dotted
    return None


class MethodAnalyzer:
    """Method call and super() analysis."""

    def __init__(
        self,
        ctx: SemanticContext,
        type_ops: TypeOperations,
        protocols: ProtocolChecker,
        compat: TypeCompatibility,
        deduction: LocalTypeDeduction,
    ):
        self.ctx = ctx
        self.type_ops = type_ops
        self.protocols = protocols
        self.compat = compat
        self.deduction = deduction
        # Set after construction to break circular dep (expr <-> calls <-> methods)
        self.expr: ExpressionAnalyzer
        self.calls: CallAnalyzer

    def _is_readonly_method(self, obj_type: TpyType, method_name: str) -> bool:
        """Check if a method is readonly on the given type (via RecordInfo)."""
        record = self.ctx.registry.get_record_for_type(obj_type)
        if record is None:
            return False
        overloads = record.get_method_overloads(method_name)
        return bool(overloads) and all(m.is_readonly for m in overloads)

    def _infer_pending_container_element(
        self,
        expr: TpyMethodCall,
        obj_type: TpyType,
        builtin_qname: str,
        literal_id: int,
        infer_fn: Callable,
        make_pending: Callable,
        literals_dict: dict,
    ) -> TpyType | None:
        """Infer element type for a pending container from method arg types.

        Looks up the method's FunctionInfo, finds params that involve the
        container's type parameters, pre-analyzes the corresponding args,
        and infers element types. Returns the updated obj_type if changed.
        """
        record = self.ctx.registry.get_builtin_record(builtin_qname)
        if not record or not record.type_params:
            return None
        type_param_names = set(record.type_params)
        overloads = record.get_method_overloads(expr.method)
        if not overloads:
            return None

        # Find an overload with matching arity that has type-param-bearing params
        for overload in overloads:
            if len(overload.params) != len(expr.args):
                continue
            # Identify which args correspond to type-parameter-bearing params
            inferring_indices: list[int] = []
            for i, param in enumerate(overload.params):
                if contains_type_param(param.type, type_param_names):
                    inferring_indices.append(i)
            if not inferring_indices:
                continue

            # Pre-analyze all args (needed for _check_and_coerce_args reuse).
            # analyze_call_arg (not analyze_expr) so a `*xs` arg yields the
            # unpacked element type instead of hitting the structural
            # analyzer's "Unknown expression type" catch-all; the non-variadic
            # reject gate in _check_args_or_pack_varargs still rejects it.
            pre_analyzed = [self.expr.analyze_call_arg(arg)
                            for arg in expr.args]

            # Infer element type from params that directly carry a type param
            # (T or Own[T]). Params with nested type params like Iterable[Own[T]]
            # are detected by contains_type_param but not handled here --
            # inference from those would need protocol-level element type extraction.
            for i in inferring_indices:
                arg_type = pre_analyzed[i]
                param_type = unwrap_ref_type(overload.params[i].type)
                if isinstance(param_type, OwnType) and isinstance(param_type.wrapped, TypeParamRef):
                    infer_fn(expr.obj, arg_type, expr.args[i])
                elif isinstance(param_type, TypeParamRef):
                    infer_fn(expr.obj, arg_type, expr.args[i])

            # Update obj_type if element type changed
            info = literals_dict.get(literal_id)
            if info and not isinstance(info.element_type, UnknownElementType):
                if info.element_type != obj_type.get_element_type():
                    obj_type = make_pending(info.element_type, literal_id)
                    self.ctx.set_expr_type(expr.obj, obj_type)
                    if isinstance(expr.obj, TpyName):
                        if self.ctx.func.current_scope:
                            self.ctx.func.current_scope.define(expr.obj.name, obj_type)
                        if self.ctx.func.current_ns:
                            self.ctx.func.current_ns.bind_variable(expr.obj.name, obj_type)
            self.ctx.func.pre_analyzed_method_args[expr] = pre_analyzed
            return obj_type
        return None

    def _check_args_or_pack_varargs(
        self, expr: TpyMethodCall,
        resolved: FunctionInfo,
        arg_types: list[TpyType] | None = None,
    ) -> None:
        """Dispatch arg checking based on whether the method has *args.

        Variadic methods route through `_analyze_and_pack_varargs` (mirrors
        the free-function path). The pre-analyzed cache is dropped because
        it holds types analyzed against Span[T], not the element type T
        the packer needs. ``arg_types`` is ignored on the variadic path --
        the packer re-analyzes every arg from scratch.
        """
        if resolved.has_variadic:
            self.ctx.func.pre_analyzed_method_args.pop(expr, None)
            self.calls._analyze_and_pack_varargs(expr, resolved)
        else:
            # `*unpack` is only valid at a variadic param position; a
            # non-variadic method must reject it (mirrors the free-function
            # guard in CallAnalyzer._typecheck_call_args). Without this the
            # pre-analyzed element type would be silently coerced as if the
            # unpack were a single positional arg.
            for arg in expr.args:
                if isinstance(arg, TpyStarUnpack):
                    raise self.ctx.error(
                        f"Cannot use *unpacking: '{resolved.name}' "
                        f"does not accept *args", arg)
            self._check_and_coerce_args(expr, resolved.params, arg_types,
                                        target_is_readonly=resolved.is_readonly)

    def _check_and_coerce_args(
        self, expr: TpyMethodCall,
        params: list[tuple[str, TpyType]],
        arg_types: list[TpyType] | None = None,
        target_is_readonly: bool = False,
    ) -> None:
        """Analyze, ownership-check, and coerce method arguments in place.

        If arg_types is None, each arg is analyzed with a type hint from the
        corresponding param (using pre-analyzed types from empty list inference
        when available). Otherwise pre-analyzed arg_types are used.
        """
        pre = self.ctx.func.pre_analyzed_method_args.pop(expr, None)
        for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, params)):
            if arg_types is not None:
                at = arg_types[i]
            elif pre is not None and i < len(pre):
                at = pre[i]
            else:
                at = self.expr.analyze_expr_with_hint(arg, ptype)
            self.calls._maybe_coerce_empty_list_to_protocol(at, ptype)
            at = self.calls._restore_readonly_arg(arg, at, target_is_readonly)
            self.calls.check_own_param(arg, at, pname, ptype)
            self.calls.mark_pending_arg_context(arg, at, ptype)
            expr.args[i] = self.compat.coerce_expr(arg, at, ptype, f"argument '{pname}'",
                                                    coercion_ctx=CoercionContext.ARG)

    def _resolve_and_check_args(
        self, expr: TpyMethodCall,
        overloads: list[FunctionInfo],
        type_subst: dict[str, TpyType | int],
        is_readonly_receiver: bool = False,
        is_consuming_receiver: bool = False,
    ) -> TpyType:
        """Resolve overloads, substitute type params, check arg count, and coerce args.

        Sets expr.resolved_function_info. Returns the return type.
        """
        # Pack **kwargs into TypedDict construction for methods with **kwargs param
        if len(overloads) == 1 and overloads[0].kwarg_name:
            func = overloads[0]
            resolved_func = (self.type_ops.substitute_method_type_params(func, type_subst)
                             if type_subst else func)
            self.calls._pack_kwargs_into_typed_dict_method(expr, resolved_func)
        elif expr.double_star_unpack is not None:
            raise self.ctx.error(
                f"'{expr.method}' does not accept **kwargs", expr)

        # Resolve kwargs before arity check. For the single-overload path we
        # expand kwargs here; the multi-overload path defers resolution to
        # after overload resolution so kwargs can participate in tier ranking.
        sole = overloads[0] if len(overloads) == 1 else None
        has_kwonly = any(p.keyword_only for p in (sole.params if sole else []))
        # A default with no C++ spelling must be filled here or the emitted
        # call drops the argument entirely.
        needs_fill = sole.materializes_defaults if sole else False
        if len(overloads) == 1 and (expr.kwargs or has_kwonly or needs_fill):
            target = overloads[0]
            resolved_target = (self.type_ops.substitute_method_type_params(target, type_subst)
                               if type_subst else target)
            expr.args = resolve_kwargs(
                expr.args, expr.kwargs, resolved_target.params, expr.method,
                lambda msg: self.ctx.error(msg, expr),
                call_loc=expr.loc, is_member=True,
            )
            expr.kwargs = {}

        if len(overloads) == 1:
            unresolved = overloads[0]
            resolved = (self.type_ops.substitute_method_type_params(unresolved, type_subst)
                        if type_subst else unresolved)
            if len(expr.args) < resolved.min_args or len(expr.args) > resolved.max_args:
                raise self.ctx.error(
                    arity_error_msg(expr.method, resolved.min_args, resolved.max_args, len(expr.args)),
                    expr)
            expr.resolved_function_info = resolved
            self._check_args_or_pack_varargs(expr, resolved)
            if type_subst:
                validate_generic_defaults(
                    expr.args, unresolved, type_subst, self.type_ops,
                    lambda msg: self.ctx.error(msg, expr))
        else:
            resolved_overloads = [
                self.type_ops.substitute_method_type_params(m, type_subst) if type_subst else m
                for m in overloads
            ]
            arg_types = self.calls._probe_candidate_args(
                expr, expr.args, resolved_overloads)
            kwarg_types: dict[str, TpyType] | None = None
            if expr.kwargs:
                kwarg_types = {k: self.expr.analyze_expr(v) for k, v in expr.kwargs.items()}
            enriched_types = _enrich_literal_types(arg_types, expr.args, resolved_overloads)
            try:
                resolved = resolve_overload(
                    resolved_overloads, enriched_types,
                    protocol_checker=self.protocols.type_conforms_to_protocol,
                    protocol_classifier=self.protocols.classify_protocol_conformance,
                    deref_checker=self.type_ops.get_deref_coercion_target,
                    default_int_type=self.ctx.default_int_type,
                    subclass_checker=self.ctx.registry.is_subclass_of,
                    is_readonly_receiver=is_readonly_receiver,
                    is_consuming_receiver=is_consuming_receiver,
                    type_ops=self.type_ops,
                    kwarg_types=kwarg_types,
                )
            except OverloadAmbiguityError as e:
                sigs = "; ".join(
                    f"{c.name}({', '.join(str(p.type) for p in c.params)})"
                    for c in e.candidates
                )
                raise self.ctx.error(
                    f"Ambiguous overload for '{expr.method}': "
                    f"multiple candidates match equally: {sigs}", expr)
            if resolved is None:
                if kwarg_types:
                    accepted_names = {p.name for o in overloads for p in o.params}
                    for kw_name in kwarg_types:
                        if kw_name not in accepted_names:
                            raise self.ctx.error(
                                f"'{expr.method}' got unexpected keyword argument '{kw_name}'",
                                expr)
                arg_strs = ", ".join(str(t) for t in arg_types)
                raise self.ctx.error(
                    f"No matching overload for '{expr.method}' with argument types ({arg_strs})", expr)
            expr.resolved_function_info = resolved
            coerce_arg_types: list[TpyType] | None = arg_types
            if expr.kwargs or resolved.materializes_defaults:
                # Winner picked; expand kwargs into positional slots against its
                # signature. A default with no C++ spelling must be filled even
                # with no kwargs, or the emitted call is an argument short.
                expr.args = resolve_kwargs(
                    expr.args, expr.kwargs, resolved.params, expr.method,
                    lambda msg: self.ctx.error(msg, expr),
                    call_loc=expr.loc, is_member=True,
                )
                expr.kwargs = {}
                # Pre-analyzed types no longer align with expanded expr.args;
                # discard any stale empty-list inference cache so
                # _check_and_coerce_args re-analyzes every arg with a param hint.
                self.ctx.func.pre_analyzed_method_args.pop(expr, None)
                coerce_arg_types = None
            self._check_args_or_pack_varargs(expr, resolved, coerce_arg_types)
            # Overloaded methods with generic defaults: find unresolved counterpart
            if type_subst:
                idx = resolved_overloads.index(resolved)
                validate_generic_defaults(
                    expr.args, overloads[idx], type_subst, self.type_ops,
                    lambda msg: self.ctx.error(msg, expr))

        # The one funnel every dispatch path reaches (instance call, generic
        # method, `super()`), and the only place carrying the class-level
        # substitution merged with the method-level one -- so it is where a
        # method body's owning-slot copy obligations are answered.
        if type_subst and expr.resolved_function_info is not None:
            self.ctx.record_own_copy_instantiation(
                expr.resolved_function_info.root, type_subst)

        self.calls._check_borrow_arg_conflicts(expr)
        self.calls._check_loop_var_arg_mutation(expr)
        self.calls._record_mutation_call_edges(expr)
        self.calls._check_error_return_handled(expr, resolved)
        return resolved.return_type

    @staticmethod
    def _analyze_super_call_static(ctx: SemanticContext, expr: TpyCall) -> TpyType:
        """Analyze a super() call (static method for use from CallAnalyzer).

        super() can only be called:
        - Inside a method (not at module level)
        - In a class that has a parent class
        - Without arguments (Python 3 style)

        Returns a SuperType that wraps the parent class type.
        """
        # Validate context: must be in a method
        if ctx.func.current_function is None or not isinstance(ctx.func.current_function, TpyFunction):
            raise ctx.error("super() can only be used inside a method", expr)

        if not ctx.func.current_function.is_method:
            raise ctx.error("super() can only be used inside a method", expr)

        if ctx.func.current_function.is_staticmethod:
            raise ctx.error("super() cannot be used in a static method", expr)

        # Validate context: must have a current record
        if ctx.record_ctx.record is None:
            raise ctx.error("super() can only be used inside a class method", expr)

        # Validate: class must have at least one parent
        record_info = ctx.registry.get_record(ctx.record_ctx.record.name)
        if record_info is None or not record_info.parents:
            raise ctx.error(
                f"super() requires a parent class, but '{ctx.record_ctx.record.name}' has no parent",
                expr
            )

        # Validate: no arguments (Python 3 style only)
        if expr.args:
            raise ctx.error("super() takes no arguments (Python 3 style)", expr)

        # For multi-base classes, parent_type is a placeholder -- _analyze_super_method_call
        # re-resolves it per method name. Single-base path uses it directly.
        return SuperType(record_info.parents[0], ctx.record_ctx.record.name)

    def _resolve_super_parent_type(self, expr: TpyMethodCall, super_type: SuperType) -> TpyType:
        """Pick which ancestor super().<method>() dispatches to.

        Single-base uses super_type.parent_type directly. Multi-base (D22 v2.3)
        walks the child's C3 MRO and returns the first ancestor whose own method
        table defines `method` (matching Python's __dict__ walk -- inherited
        methods don't count). super().__del__() is rejected; C++ invokes each
        base's destructor automatically. Single-hop only; see LANGUAGE_FEATURES
        "Limitation: single-hop super()" for why TPy can't replicate Python's
        full cooperative chain under static dispatch.
        """
        child_rec = self.ctx.registry.get_record(super_type.child_record_name)
        if child_rec is None or len(child_rec.parents) < 2:
            return super_type.parent_type

        if expr.method == "__del__":
            raise self.ctx.error(
                f"super().__del__() is not allowed in multi-base class "
                f"'{super_type.child_record_name}'. C++ invokes each base's destructor "
                f"automatically; remove this call.",
                expr
            )

        for anc_type in child_rec.mro_ancestors:
            anc_info = self.ctx.registry.get_record_for_type(anc_type)
            if anc_info is None:
                continue
            if anc_info.get_method_overloads(expr.method):
                return anc_type

        raise self.ctx.error(
            f"No base of '{super_type.child_record_name}' defines method "
            f"'{expr.method}'",
            expr
        )

    def _try_resolve_method(self, expr: TpyMethodCall, obj_type: TpyType,
                            is_readonly_receiver: bool = False,
                            is_consuming_receiver: bool = False) -> TpyType | None:
        """Try to resolve method on obj_type. Returns return type or None."""
        result = self._analyze_instance_method(expr, obj_type, is_readonly_receiver, is_consuming_receiver)
        if result is not None:
            return result
        # A value field makes the saved brackets indexing, never method type args.
        if expr.subscript_callee is not None:
            has_field = False
            if isinstance(obj_type, NominalType):
                record = self.ctx.registry.receiver_record(obj_type)
                has_field = (record is not None
                    and (self.protocols.lookup_record_field(record, expr.method) is not None
                         or self.protocols.lookup_record_property(record, expr.method) is not None))
            elif isinstance(obj_type, TypeParamRef):
                bound = self.type_ops.get_type_param_bound(obj_type.name)
                if bound is not None and is_protocol_type(bound):
                    protocol = protocol_info_of(bound)
                    has_field = protocol is not None and any(
                        name == expr.method for name, _ in protocol.fields or [])
            if has_field:
                subscript = expr.subscript_callee
                field = subscript.obj
                receiver_type = self.ctx.get_raw_expr_type(expr.obj)
                assert receiver_type is not None
                field_type = self.expr._analyze_field_access(field, receiver_type)
                self.ctx.set_expr_type(field, field_type)
                callee_type = self.expr._analyze_subscript(subscript, field_type)
                self.ctx.set_expr_type(subscript, callee_type)
                expr.obj = subscript
                expr.method = "__call__"
                expr.subscript_callee = None
                expr.type_args = ()
                expr.type_args_parse_error = None
                return self.analyze_method_call(expr, callee_type)
        result = self._analyze_protocol_or_bound_method(expr, obj_type)
        if result is not None:
            return result
        # Callable-typed field invocation: obj.field(args) where field is Callable
        result = self._try_callable_field_call(expr, obj_type)
        if result is not None:
            return result
        return None

    def _try_callable_field_call(self, expr: TpyMethodCall, obj_type: TpyType) -> TpyType | None:
        """Check if expr.method is a Callable-typed field and analyze the call."""
        if not isinstance(obj_type, NominalType):
            return None
        rec = self.ctx.registry.get_record(obj_type.name)
        if rec is None:
            return None
        callable_type = None
        is_optional_field = False
        for fld in rec.fields:
            if fld.name == expr.method:
                # Send/Sync markers constrain stores into the field, not calls
                fld_type = unwrap_send_sync(fld.type)
                if isinstance(fld_type, CallableType):
                    callable_type = fld_type
                elif isinstance(fld_type, OptionalType) and isinstance(
                        unwrap_send_sync(fld_type.inner), CallableType):
                    callable_type = unwrap_send_sync(fld_type.inner)
                    is_optional_field = True
                break
        if callable_type is None:
            return None
        # Warn if calling Optional[Callable] field without narrowing
        if is_optional_field:
            narrowing_key = _expr_to_narrowing_key(expr.obj)
            if narrowing_key is not None:
                narrowing_key = f"{narrowing_key}.{expr.method}"
            if narrowing_key is None or narrowing_key not in self.ctx.func.narrowed_types:
                self.ctx.warning(OPTIONAL_NONE_ACCESS_WARNING, expr)
                expr.needs_optional_runtime_check = True
        ret = self.calls.analyze_callable_value_call(
            expr, callable_type, expr.method, f"Callable field '{expr.method}'")
        expr.is_callable_field = True
        return ret

    def analyze_method_call(self, expr: TpyMethodCall,
                            obj_type: TpyType | None = None) -> TpyType:
        """Analyze a method call."""
        # A `.cancel()` read of a bound coroutine handle is not
        # consumption: without this restore, `c = f(); c.cancel()`
        # silences the never-consumed warning while the body never runs.
        # Task/other receivers are not in unread_coro_locals -- no-op.
        _coro_unread = None
        if expr.method == "cancel" and isinstance(expr.obj, TpyName):
            _coro_unread = self.ctx.func.unread_coro_locals.get(expr.obj.name)
        self._mark_coro_started(expr)
        try:
            result = self._analyze_method_call_impl(expr, obj_type)
            # Walks after sema must see only the resolved receiver, never its alternative.
            expr.subscript_callee = None
            return result
        finally:
            if _coro_unread is not None:
                self.ctx.func.unread_coro_locals[expr.obj.name] = _coro_unread

    def _mark_coro_started(self, expr: TpyMethodCall) -> None:
        """A method call on a bound coroutine frame may start it (every
        method a frame has changes it): record it, so a later move of the
        started frame is rejected. An erased handle is a pointer to its
        frame, which a move of the handle leaves in place."""
        if not isinstance(expr.obj, TpyName) or self.ctx.func.current_scope is None:
            return
        t = self.ctx.func.current_scope.lookup(expr.obj.name)
        inner = unwrap_readonly(unwrap_own(unwrap_ref_type(t))) if t else None
        if isinstance(inner, ConcreteCoroType):
            self.ctx.func.started_coro_locals.setdefault(
                expr.obj.name, expr.loc.line if expr.loc else 0)

    def _analyze_method_call_impl(self, expr: TpyMethodCall,
                                  obj_type: TpyType | None = None) -> TpyType:
        if isinstance(expr.obj, TpyName):
            # ClassName.staticmethod() pattern
            result = self._analyze_static_method_call(expr)
            if result is not None:
                return result

            # Nested type constructor: Container.Inner(...) or Container.Kind(value)
            if self.ctx.func.current_ns:
                binding = self.ctx.func.current_ns.lookup(expr.obj.name)
                if binding and binding.kind in (BindingKind.RECORD, BindingKind.IMPORTED_NAME):
                    nested_result = self._analyze_nested_type_call(expr)
                    if nested_result is not None:
                        return nested_result

            # Reject method calls on enum types (enums have no class methods)
            if self.ctx.func.current_ns:
                binding = self.ctx.func.current_ns.lookup(expr.obj.name)
                if binding and binding.kind == BindingKind.ENUM:
                    raise self.ctx.error(
                        f"Enum type '{expr.obj.name}' has no method '{expr.method}'",
                        expr,
                    )

            # module.function() pattern (import X -> X.func())
            result = self._analyze_module_method_call(expr)
            if result is not None:
                return result

        # Nested type constructor via chained access: Outer.Mid.Deep(...)
        if isinstance(expr.obj, TpyFieldAccess):
            parent_dotted = _resolve_dotted_record_chain(expr.obj, self.ctx)
            if parent_dotted is not None:
                dotted = f"{parent_dotted}.{expr.method}"
                nested_record = self.ctx.registry.get_record(dotted)
                if nested_record is not None:
                    fake_call = TpyCall(
                        func=TpyName(name=dotted, loc=expr.loc),
                        args=expr.args,
                        kwargs=expr.kwargs,
                        type_args=expr.type_args,
                        type_args_parse_error=expr.type_args_parse_error,
                        loc=expr.loc,
                    )
                    expr.is_nested_constructor = True
                    expr.nested_type_name = dotted
                    result = self.calls._analyze_record_constructor(fake_call, nested_record)
                    self.ctx.set_expr_type(expr, result)
                    return result

        # Module-qualified static method call: m.Foo.method() or pkg.sub.Foo.method().
        # Receiver shape is TpyFieldAccess(<module-chain>, ClassName); none of the
        # other TpyFieldAccess branches recognise it because the inner is a module
        # binding (not a record chain) and the leaf is a class (not a function).
        # `user_module_call` is only set for the static-method dispatch -- the
        # unbound-self path (BaseN.method(self, ...)) uses `this->Parent::method`
        # codegen that doesn't go through the module-qualified namespace.
        if isinstance(expr.obj, TpyFieldAccess):
            resolved = self._try_resolve_module_qualified_class(expr.obj)
            if resolved is not None:
                module_name, _class_short, record_info = resolved
                result = self._dispatch_static_method_call(expr, record_info)
                if result is not None:
                    if expr.is_static_call:
                        expr.user_module_call = module_name
                    return result

        # Dotted module access: X.Y.func(), X.Y.Z.func(), etc.
        if isinstance(expr.obj, TpyFieldAccess):
            dotted_name = self._try_resolve_dotted_module(expr.obj)
            if dotted_name:
                flat_obj = TpyName(name=dotted_name, loc=expr.obj.loc)
                flat_expr = TpyMethodCall(
                    obj=flat_obj, method=expr.method, args=expr.args,
                    kwargs=expr.kwargs,
                    type_args=expr.type_args,
                    type_args_parse_error=expr.type_args_parse_error,
                    loc=expr.loc,
                )
                result = self._analyze_module_method_call(flat_expr, module_name=dotted_name)
                if result is not None:
                    expr.args = flat_expr.args
                    expr.kwargs = flat_expr.kwargs
                    expr.builtin_module_call = flat_expr.builtin_module_call
                    expr.user_module_call = flat_expr.user_module_call
                    expr.resolved_function_info = flat_expr.resolved_function_info
                    expr.inferred_type_args = flat_expr.inferred_type_args
                    expr.representational_subst_params = flat_expr.representational_subst_params
                    return result

        if obj_type is None:
            obj_type = self.expr.analyze_expr(expr.obj)
        callable_type = unwrap_qualifiers(obj_type)
        if expr.method == "__call__" and isinstance(callable_type, CallableType):
            return self.calls.analyze_callable_value_call(
                expr, callable_type, "<expr>", "Callable type")

        # super().method() calls: the receiver resolves to SuperType via the
        # builtins.super qname dispatch in calls.py (supersedes the old bare-string
        # intercept so user `def super()` shadows normally).
        if isinstance(obj_type, SuperType):
            return self._analyze_super_method_call(expr, obj_type)

        # Pending generic instance: accumulate constraints from method calls
        if isinstance(obj_type, PendingGenericInstanceType):
            return self._analyze_pending_generic_method_call(expr, obj_type)

        # Unwrap RefType, ReadonlyType, remembering the flag for enforcement.
        # Also check declared scope type: isinstance narrowing may strip ReadonlyType
        # from the expr type while the scope binding preserves it.
        # Send/Sync markers (canonically outermost) constrain stores into the
        # slot, not receiver dispatch -- strip before the readonly check.
        obj_type = unwrap_send_sync(unwrap_ref_type(obj_type))
        is_readonly_receiver = receiver_is_readonly(self.ctx, expr.obj, obj_type)
        if isinstance(obj_type, ReadonlyType):
            obj_type = obj_type.wrapped

        # General method calls always prefer borrowing overloads.
        # Consuming __iter__ overloads are selected at the call site by
        # _consuming_iter_wrap in thir/lower/expressions.py.
        is_consuming_receiver = False

        if isinstance(obj_type, OwnType):
            obj_type = obj_type.wrapped
        elif isinstance(obj_type, OptionalType):
            if obj_type.inner.is_value_type():
                raise self.ctx.error(f"Cannot call method '{expr.method}' on type {obj_type}", expr)
            self.ctx.warning(OPTIONAL_NONE_ACCESS_WARNING, expr)
            expr.needs_optional_runtime_check = True
            obj_type = obj_type.inner

        # List mutation tracking (before deref chain -- applies to direct list types only)
        if isinstance(obj_type, PendingListType) or is_list(obj_type):
            if not self._is_readonly_method(obj_type, expr.method):
                mark_pending_list_mutated(self.ctx, expr.obj, obj_type)

        # Pending container element type inference from method args.
        # For any method on a pending container, check if params involve the
        # container's type parameters. If so, pre-analyze the args and infer
        # the element type (generalizes append/insert/add/etc.).
        if isinstance(obj_type, PendingListType) and expr.args:
            obj_type = self._infer_pending_container_element(
                expr, obj_type, "builtins.list", obj_type.literal_id,
                self.deduction.infer_empty_list_element_type,
                lambda elem, lid: PendingListType(elem, obj_type.size, lid),
                self.ctx.list_literals,
            ) or obj_type
        elif isinstance(obj_type, PendingSetType) and expr.args:
            obj_type = self._infer_pending_container_element(
                expr, obj_type, "builtins.set", obj_type.literal_id,
                self.deduction.infer_set_element_type,
                lambda elem, lid: PendingSetType(elem, lid),
                self.ctx.set_literals,
            ) or obj_type

        # Reject storing a temporary rvalue into a view-typed container element
        # (list[StrView].append(make()), set[BytesView].add(...), list.insert):
        # the temporary dies at end-of-statement, leaving the container -- which
        # outlives the statement -- a dangling view. Param passing is safe (the
        # temp outlives the call); only durable element storage is the hazard.
        # Narrow to rvalue temporaries: a param/local-derived view stored into an
        # escaping container is the deeper field-lifetime gap (tracked in BUGS.md).
        if (expr.method in _VIEW_ELEM_INSERTS
                and (is_list(obj_type) or is_set(obj_type)
                     or isinstance(obj_type, (PendingListType, PendingSetType)))):
            elem_t = obj_type.get_element_type()
            ai = _VIEW_ELEM_INSERTS[expr.method]
            if (elem_t is not None and is_borrowing_view_type(unwrap_readonly(elem_t))
                    and ai < len(expr.args)):
                arg = expr.args[ai]
                if view_source_is_temporary(
                        arg.expr if isinstance(arg, TpyCoerce) else arg):
                    raise self.ctx.error(
                        f"Cannot store a temporary in '{expr.method}' on a "
                        f"{elem_t} container; the backing storage is destroyed at "
                        f"end-of-statement -- store an owned str/bytes element",
                        expr,
                    )

        # Borrow conflict: structural mutation on a container with element-level borrows.
        check_receiver_call_loans(self.ctx, expr.obj, obj_type, expr.method, expr)

        # Deref chain -- resolves through Ptr (mutable and readonly) and any Deref[T] type
        original_type = obj_type
        current_type = obj_type
        deref_depth = 0
        while deref_depth <= 8:
            result = self._try_resolve_method(expr, current_type, is_readonly_receiver, is_consuming_receiver)
            if (result is not None
                    and isinstance(original_type, PENDING_CONTAINER_TYPES)
                    and not isinstance(result, (IntLiteralType, FloatLiteralType))):
                # A pending receiver binds its type params to its literal
                # element types; a type the method builds from them (the list
                # `copy()` returns) is concrete and resolves them as the
                # receiver would by default. A bare element read stays a
                # literal, which the reading site resolves in context.
                result = resolve_int_literals(result, self.ctx.default_int_for_literal)
            if result is not None:
                info = expr.resolved_function_info
                if (info is not None and info.is_callable_value
                        and not expr.is_callable_field):
                    return result
                expr.deref_depth = deref_depth
                if deref_depth > 0 and isinstance(original_type, PtrType):
                    obj_key = _expr_to_narrowing_key(expr.obj)
                    if obj_key is not None:
                        if obj_key in self.ctx.func.non_null_ptr_vars:
                            expr.ptr_non_null = True
                        if expr.loc:
                            self.ctx.ptr_deref_facts[
                                (expr.loc.line, obj_key)
                            ] = expr.ptr_non_null
                        # Post-access narrowing: queued for flush at statement
                        # boundary (see pending_non_null_ptr_vars docstring).
                        self.ctx.func.pending_non_null_ptr_vars.add(obj_key)
                # Enforce readonly: cannot call non-readonly method on readonly
                # receiver. Callable fields are exempt: the synthetic fi is
                # non-readonly to model the CALLBACK's effects on its args,
                # but invoking the field neither mutates the receiver nor
                # needs a mutable one (std::function::operator() is const).
                if is_readonly_receiver and not expr.is_callable_field:
                    info = expr.resolved_function_info
                    if info is not None and not info.is_readonly:
                        raise self.ctx.error(
                            f"Cannot call non-readonly method '{expr.method}' on readonly reference",
                            expr)
                # Enforce consuming methods: receiver must be a local variable
                info = expr.resolved_function_info
                if info is not None and info.is_consuming:
                    self._validate_consuming_call(expr)
                # Track non-readonly method calls on for-each loop variables
                # and string view sources (receiver mutation invalidates views)
                info = expr.resolved_function_info
                # The mutable clone of an @auto_readonly accessor (Box.get / Rc.get /
                # Deref) does not mutate its receiver -- only a mutation *through* its
                # borrowed result does. Demoting here would force every read-only use
                # (`return o.b.get().v`) to a mutable receiver. The actual mutation is
                # rooted back to the receiver at the mutation site, where
                # _root_name_of_expr is transparent to the accessor call.
                # A method call through an `unsafe_interior_mutable` field (e.g.
                # `self._cell.incr_strong()`) mutates bookkeeping the owner
                # declared outside its readonly boundary -- it must not demote
                # the enclosing method. Reassigning the slot is a separate path
                # (assignment enforcement on the receiver) and stays rejected.
                interior_receiver = (
                    isinstance(expr.obj, TpyFieldAccess)
                    and expr.obj.accessed_field_is_interior
                )
                if (info is not None and not info.is_readonly
                        and not info.is_auto_readonly_mutable_clone
                        and not expr.is_callable_field
                        and not interior_receiver):
                    # A select receiver is each operand it may pick, and
                    # each is credited exactly as a single receiver would be.
                    # The self-rooted call edge is `_record_mutation_call_edges`'s.
                    credit_receiver_mutation(self.ctx, expr.obj, obj_type,
                                             expr.method, edges_recorded=True)
                return result

            deref_target = self.expr.get_deref_target_type(
                current_type, is_readonly=is_readonly_receiver)
            if deref_target is None:
                break
            # __deref__() may return readonly[T]; unwrap and propagate
            # readonly so method calls enforce const semantics.
            if isinstance(deref_target, ReadonlyType):
                is_readonly_receiver = True
                deref_target = deref_target.wrapped
            # Deref-view narrowing: inside `if isinstance(rc, Dog):` the peeled
            # payload is narrowed to the subclass so `bark` (a Dog method, not
            # on the Pet protocol) resolves. Tag the node for the codegen cast.
            nsub = deref_view_narrowed(self.ctx, expr.obj, deref_target)
            if nsub is not None:
                expr.deref_narrowed_to = nsub
                current_type = nsub
            else:
                current_type = deref_target
            deref_depth += 1

        raise self.ctx.error(f"Cannot call method '{expr.method}' on type {original_type}", expr)

    def _validate_consuming_call(self, expr: TpyMethodCall) -> None:
        """Validate a consuming method call (self: Own[Self]).

        The receiver must be a local variable (not a field or other expression).
        Temporaries are also allowed (e.g. Box(value).take()).
        After the call, the variable is marked as consumed.
        """
        if expr.deref_depth > 0:
            raise self.ctx.error(
                f"Cannot call consuming method '{expr.method}' through a Deref chain; "
                f"consuming methods must be called directly on the owning type",
                expr,
            )
        if isinstance(expr.obj, TpyFieldAccess):
            raise self.ctx.error(
                f"Cannot call consuming method '{expr.method}' on a field; "
                f"only local variables and temporaries are allowed",
                expr,
            )
        if isinstance(expr.obj, TpyName):
            name = expr.obj.name
            if name == "self":
                raise self.ctx.error(
                    f"Cannot call consuming method '{expr.method}' on 'self'; "
                    f"only local variables and temporaries are allowed",
                    expr,
                )
            # Reject consuming through pointers -- pointer doesn't own the pointee
            obj_type = self.ctx.func.current_scope.lookup(name)
            if obj_type is not None and (isinstance(obj_type, PtrType)
                                         or isinstance(unwrap_readonly(obj_type), PtrType)):
                raise self.ctx.error(
                    f"Cannot call consuming method '{expr.method}' on pointer '{name}'; "
                    f"pointers do not own the pointee",
                    expr,
                )
            # Reject consuming outer variables inside a loop -- the variable
            # won't be re-bound on the next iteration, causing use-after-move.
            if self.ctx.func.loop_depth > 0:
                var_depth = self.ctx.func.var_scope_depth.get(name, 0)
                loop_scope_depth = self.ctx.func.current_scope.depth
                if var_depth < loop_scope_depth:
                    raise self.ctx.error(
                        f"Cannot consume '{name}' inside a loop; "
                        f"the variable is not re-bound each iteration",
                        expr,
                    )
            # Use-after-consume is already caught by _analyze_name before we get here.
            self.ctx.func.consumed_vars.add(name)

    def _analyze_nested_type_call(self, expr: TpyMethodCall) -> TpyType | None:
        """Check for Outer.Inner(...) nested type constructor call. Returns type or None."""
        assert isinstance(expr.obj, TpyName)
        dotted = f"{expr.obj.name}.{expr.method}"
        # Nested record constructor
        nested_record = self.ctx.registry.get_record(dotted)
        if nested_record is not None:
            # Rewrite as a direct constructor call
            fake_call = TpyCall(
                func=TpyName(name=dotted, loc=expr.loc),
                args=expr.args,
                kwargs=expr.kwargs,
                type_args=expr.type_args,
                type_args_parse_error=expr.type_args_parse_error,
                loc=expr.loc,
            )
            # Mark the original expr so codegen knows it's a constructor
            expr.is_nested_constructor = True
            expr.nested_type_name = dotted
            result = self.calls._analyze_record_constructor(fake_call, nested_record)
            # Copy type info back
            self.ctx.set_expr_type(expr, result)
            return result
        # Nested enum constructor (from_value)
        nested_enum = self.ctx.registry.get_enum(dotted)
        if nested_enum is not None:
            # Container.Kind(1) -> Container::Kind from_value
            if len(expr.args) != 1 or expr.kwargs:
                raise self.ctx.error(
                    f"Nested enum '{dotted}' constructor takes exactly 1 positional argument",
                    expr)
            arg_type = self.expr.analyze_expr(expr.args[0])
            if not is_any_int_type(arg_type):
                raise self.ctx.error(
                    f"Cannot construct '{dotted}' from '{arg_type}', "
                    f"expected an integer type",
                    expr)
            expr.is_nested_enum_constructor = True
            expr.nested_type_name = dotted
            return nested_enum
        return None

    def _analyze_static_method_call(self, expr: TpyMethodCall) -> TpyType | None:
        """Check for ClassName.staticmethod() or BaseN.method(self, ...) pattern.

        Returns the call's type, or None if ClassName is not a record binding
        (fall-through to other method-call dispatch).

        Routes (checked in order):
        1. ClassName.staticmethod(args) -- static-method call.
        2. BaseN.method(self, args) where BaseN is an ancestor of the current
           record: rebinds to instance-method machinery, dispatches statically
           to BaseN's definition. First arg must literally be 'self'.
        """
        assert isinstance(expr.obj, TpyName)
        if self.ctx.func.current_ns is None:
            return None

        record_info = None
        binding = self.ctx.func.current_ns.lookup(expr.obj.name)
        if binding and binding.kind == BindingKind.RECORD:
            # Read the record off the binding rather than re-deriving it from
            # the receiver's spelling: the binding is authoritative (`cls`
            # names its record, and get_record's short-name key collides
            # across modules).
            record_info = binding.record_info
        elif binding and binding.kind == BindingKind.IMPORTED_NAME:
            import_info = self.ctx.imported_names.get(expr.obj.name)
            if import_info:
                record_info = self.ctx.registry.find_record_by_qname(
                    f"{import_info[0]}.{import_info[1]}")
        elif binding and binding.kind == BindingKind.ENUM:
            # `Color.m()` / `cls.m()`: the enum's methods are its companion's.
            record_info = self.ctx.registry.receiver_record(binding.enum_type)

        if record_info is None:
            return None
        return self._dispatch_static_method_call(expr, record_info)

    def _reject_inherited_classmethod(
        self, expr: TpyMethodCall, record_info: RecordInfo,
        overloads: list[FunctionInfo],
    ) -> None:
        """Reject an INHERITED classmethod reached through a subclass.

        `cls` binds statically to the DEFINING record, so the call would
        construct the base where CPython binds the receiver and constructs the
        subclass. Both receiver spellings reach it -- a class name
        (`Derived.make()`) and an instance whose static type is the subclass
        (`derived_obj.make()`) -- so both dispatch paths call this.
        """
        if not overloads or not overloads[0].is_classmethod:
            return
        if record_info.get_method_overloads(expr.method):
            return
        owner = next(
            (a for a in self.ctx.registry.iter_ancestor_records(record_info)
             if a.get_method_overloads(expr.method)), None)
        owner_name = owner.name if owner is not None else "its base"
        raise self.ctx.error(
            f"classmethod '{expr.method}' is inherited from '{owner_name}', so "
            f"'cls' binds to '{owner_name}', not '{record_info.name}' -- this "
            f"would construct a '{owner_name}'. Call "
            f"'{owner_name}.{expr.method}(...)', or override '{expr.method}' "
            f"on '{record_info.name}'",
            expr)

    def _dispatch_static_method_call(
        self, expr: TpyMethodCall, record_info: RecordInfo,
    ) -> TpyType | None:
        """Dispatch a staticmethod / unbound-self call on a pre-resolved record.

        Walks MRO, distinguishes static from unbound-self, handles generic
        class / method type args. Shared by bare-name (`Foo.method()`) and
        module-qualified (`m.Foo.method()`) receivers.
        """
        overloads = self.ctx.registry.get_method_overloads_with_parents(
            record_info, expr.method)
        if not overloads:
            return None
        if not overloads[0].is_staticmethod:
            return self._analyze_unbound_self_method_call(expr, record_info, overloads)

        self._reject_inherited_classmethod(expr, record_info, overloads)

        if record_info.is_generic():
            return self._analyze_generic_static_method_call(expr, record_info, overloads)

        method = overloads[0]
        if method.type_params:
            # Non-generic class with method-level type params: type_args are for the method
            return self._analyze_generic_static_method_call(expr, record_info, overloads)

        if expr.type_args or expr.type_args_parse_error:
            raise self.ctx.error(
                f"'{record_info.display_name}' is not generic and does not accept type arguments",
                expr,
            )

        return_type = self._resolve_and_check_args(expr, overloads, {})
        expr.is_static_call = True
        expr.static_call_owner = record_info
        return return_type

    def _try_resolve_module_qualified_class(
        self, obj: TpyFieldAccess,
    ) -> tuple[str, str, RecordInfo] | None:
        """Resolve `obj` as `<module-chain>.ClassName` to (module_name, class_short, record_info).

        Handles both single-segment module references (`TpyName(m).Class`) and
        dotted-segment ones (`TpyFieldAccess(pkg.sub).Class`). Returns None when
        the inner receiver isn't a module binding or the named class isn't
        registered in that module.
        """
        class_short = obj.field
        if isinstance(obj.obj, TpyName):
            module_name = self._resolve_module_name(obj.obj.name)
        elif isinstance(obj.obj, TpyFieldAccess):
            module_name = self._try_resolve_dotted_module(obj.obj)
        else:
            return None
        if module_name is None:
            return None
        record_info = self.ctx.registry.find_record_by_qname(
            f"{module_name}.{class_short}")
        if record_info is None:
            enum_t = self.ctx.registry.find_enum_by_qname(
                f"{module_name}.{class_short}")
            if enum_t is not None:
                record_info = self.ctx.registry.receiver_record(enum_t)
        if record_info is None:
            return None
        return (module_name, class_short, record_info)

    def _analyze_unbound_self_method_call(
        self, expr: TpyMethodCall, record_info, overloads: list[FunctionInfo],
    ) -> TpyType:
        """Handle `BaseN.method(self, ...)` calls on an ancestor class.

        `record_info` is the class named on the left of the dot; `overloads`
        are the MRO-walked non-static overloads of `expr.method` (caller has
        already dispatched on staticmethod).

        Rules:
        - The enclosing context must be an instance method (self in scope).
        - ClassName must be a strict ancestor of the current record.
        - First arg must syntactically be the name `self` -- arbitrary unbound
          calls (e.g. `Named.describe(other_instance)`) are rejected.
        """
        parent_name = record_info.display_name
        method_name = expr.method

        # Must be inside an instance method
        current_rec_parse = self.ctx.record_ctx.record
        current_fn = self.ctx.func.current_function
        if (current_rec_parse is None
                or current_fn is None
                or not isinstance(current_fn, TpyFunction)
                or not current_fn.is_method
                or current_fn.is_staticmethod):
            raise self.ctx.error(
                f"Method '{parent_name}.{method_name}' requires an instance "
                f"(not a static method)",
                expr,
            )
        current_rec = self.ctx.registry.get_record(current_rec_parse.name)
        assert current_rec is not None, (
            f"registry missing RecordInfo for '{current_rec_parse.name}' "
            f"while analyzing its methods"
        )

        # __del__ compiles to a C++ destructor (not a callable member); __init__
        # compiles to a constructor and is only reachable as a base initializer
        # from within the child's __init__ body.
        if method_name == "__del__":
            raise self.ctx.error(
                f"'{parent_name}.__del__(self)' is not callable via the unbound-self "
                f"form. C++ invokes each base destructor automatically.",
                expr,
            )
        if method_name == "__init__" and current_fn.name != "__init__":
            raise self.ctx.error(
                f"'{parent_name}.__init__(self, ...)' can only be called inside "
                f"'__init__'",
                expr,
            )

        # Ancestor check (strict: excludes current record itself)
        if not self.ctx.registry.is_subclass_of_record(current_rec, record_info):
            raise self.ctx.error(
                # An enum cannot be subclassed, so `self.m()` means the same
                # call; on a record an override would change which one runs.
                (f"'{parent_name}.{method_name}(self, ...)' calls a method of "
                 f"'{parent_name}' itself; call 'self.{method_name}(...)' instead"
                 if current_rec is record_info and record_info.enum_companion_of
                 else f"'{parent_name}.{method_name}(self, ...)' calls a method of "
                 f"'{parent_name}' itself, which is not supported"
                 if current_rec is record_info else
                 f"'{parent_name}' is not an ancestor of '{current_rec.display_name}'; "
                 f"cannot call '{parent_name}.{method_name}(self, ...)' here"),
                expr,
            )

        # First arg must be the literal name 'self'.
        if not expr.args or not (isinstance(expr.args[0], TpyName)
                                 and expr.args[0].name == "self"):
            raise self.ctx.error(
                f"'{parent_name}.{method_name}(...)' must pass 'self' as the first "
                f"argument (e.g. '{parent_name}.{method_name}(self, ...)')",
                expr,
            )

        # Caller passed MRO-walked overloads and dispatched on staticmethod, so
        # `overloads` is the authoritative list of non-static candidates here.
        assert overloads and not overloads[0].is_staticmethod

        parent_type, type_subst = self.protocols.resolve_ancestor_instantiation(
            current_rec, record_info)

        # Rebind to instance-method machinery via a temp TpyMethodCall with
        # obj=self and self dropped from args. _resolve_and_check_args mutates
        # the temp's args (coercions), so copy them back after. self itself
        # is then dropped from expr.args so codegen sees the same shape as
        # super().method(...) -- base-qualified call with method args only.
        self_arg = expr.args[0]
        # Analyze self so downstream (mutation tracking, type queries) has a type.
        self.expr.analyze_expr(self_arg)
        fake_expr = TpyMethodCall(
            obj=self_arg,
            method=method_name,
            args=list(expr.args[1:]),
            kwargs=expr.kwargs,
            type_args=expr.type_args,
            type_args_parse_error=expr.type_args_parse_error,
            loc=expr.loc,
        )

        method_info = overloads[0]
        if method_info.is_generic():
            if len(overloads) > 1:
                raise self.ctx.error(
                    f"Overloaded generic methods are not supported for '{method_name}'",
                    expr)
            return_type = self._analyze_generic_method_call(
                fake_expr, method_info, record_info, type_subst)
        else:
            return_type = self._resolve_and_check_args(
                fake_expr, overloads, type_subst)

        # Readonly self: a @readonly context calling a non-readonly ancestor
        # method would mutate self through a const receiver.
        is_readonly_context = (
            isinstance(self.ctx.func.current_function, TpyFunction)
            and self.ctx.func.current_function.is_readonly
        )
        resolved_info = fake_expr.resolved_function_info
        if is_readonly_context and resolved_info is not None and not resolved_info.is_readonly:
            raise self.ctx.error(
                f"Cannot call non-readonly method '{method_name}' on readonly reference",
                expr)

        # Drop self from the outer expr so codegen emits Parent::method(args)
        # with args matching resolved_function_info.params shape.
        expr.args = fake_expr.args
        expr.kwargs = fake_expr.kwargs
        expr.resolved_function_info = resolved_info
        expr.inferred_type_args = fake_expr.inferred_type_args
        expr.representational_subst_params = fake_expr.representational_subst_params
        expr.unbound_self_parent_type = parent_type
        return return_type

    def _analyze_generic_static_method_call(
        self, expr: TpyMethodCall, record_info, overloads: list[FunctionInfo],
    ) -> TpyType:
        """Resolve a static method call on a generic record or a static method
        with its own type parameters.

        Creates a virtual FunctionInfo with class + method type params merged,
        then delegates to _analyze_user_function_call (same path as free generic calls).
        """
        # NOTE: picks first overload. If overloaded static methods on generic
        # classes are added, this needs overload resolution per-candidate with
        # inference (similar to _analyze_builtin_function_overloads).
        method = overloads[0]
        class_type_params = set(record_info.type_params) if record_info.type_params else set()
        new_method_params = [tp for tp in (method.type_params or []) if tp not in class_type_params]

        # Merge class type params + method's own type params into a single virtual FunctionInfo.
        # The free function path handles inference, explicit args, and bound validation.
        all_type_params = list(record_info.type_params) + new_method_params
        all_bounds = dict(record_info.type_param_bounds)
        all_bounds.update({k: v for k, v in method.type_param_bounds.items()
                          if k in set(new_method_params)})

        # Validate type arg count: must match either class params (method params inferred)
        # or all params (class + method)
        if expr.type_args:
            n_class = len(record_info.type_params)
            n_total = len(all_type_params)
            n_given = len(expr.type_args)
            if n_given != n_class and n_given != n_total:
                if new_method_params:
                    raise self.ctx.error(
                        f"'{record_info.display_name}.{method.name}' expects {n_class} class type arguments "
                        f"or {n_total} total (class + method), got {n_given}",
                        expr)
                raise self.ctx.error(
                    f"'{record_info.display_name}' expects {n_class} type arguments, got {n_given}",
                    expr)

        virtual_func = FunctionInfo(
            name=method.name, params=method.params, return_type=method.return_type,
            is_staticmethod=method.is_staticmethod,
            is_classmethod=method.is_classmethod,
            type_params=all_type_params,
            type_param_bounds=all_bounds,
            cpp_template=method.cpp_template,
            native_name=method.native_name,
            canonical_fi=method.root,
        )
        temp_call = TpyCall(func=TpyName(expr.method, loc=expr.loc), args=expr.args,
                            kwargs=expr.kwargs,
                            type_args=expr.type_args,
                            type_args_parse_error=expr.type_args_parse_error,
                            loc=expr.loc)
        result = self.calls._analyze_user_function_call(temp_call, [virtual_func])
        expr.args = temp_call.args
        expr.kwargs = temp_call.kwargs
        expr.resolved_function_info = temp_call.resolved_function_info
        expr.inferred_type_args = temp_call.inferred_type_args
        expr.representational_subst_params = temp_call.representational_subst_params
        expr.is_static_call = True
        expr.static_call_owner = record_info
        return result

    def _analyze_module_method_call(
        self, expr: TpyMethodCall, module_name: str | None = None,
    ) -> TpyType | None:
        """Check for module.function() pattern. Returns type or None if not a module call.

        If module_name is provided, skips namespace resolution (used for dotted
        module access like tpy.unsafe.func() where the module is already known).
        """
        assert isinstance(expr.obj, TpyName)

        if module_name is None:
            module_name = self._resolve_module_name(expr.obj.name)
            if module_name is None:
                return None

        module_info = self.ctx.registry.get_module(module_name)
        if module_info and module_info.functions and expr.method in module_info.functions:
            overloads = module_info.functions[expr.method]
            # asyncio.run / asyncio.create_task: ordinary module functions
            # plus the coroutine-only arg contract (CPython parity).
            _qname = f"{module_name}.{expr.method}"
            if _qname in (qnames.ASYNCIO_RUN, qnames.ASYNCIO_CREATE_TASK):
                expr.user_module_call = module_name
                temp_call = TpyCall(func=TpyName(expr.method, loc=expr.loc), args=expr.args,
                                    kwargs=expr.kwargs,
                                    type_args=expr.type_args,
                                    type_args_parse_error=expr.type_args_parse_error,
                                    loc=expr.loc)
                result = self.calls._analyze_asyncio_spawn_call(temp_call, _qname, overloads)
                expr.args = temp_call.args
                expr.kwargs = temp_call.kwargs
                expr.resolved_function_info = temp_call.resolved_function_info
                expr.inferred_type_args = temp_call.inferred_type_args
                expr.representational_subst_params = temp_call.representational_subst_params
                return result
            # Route through builtin path if the function is from a builtin module
            # or has a cpp_template (inline expansion, no C++ function body).
            is_builtin_func = (module_info.is_builtin
                               or overloads[0].is_builtin_function
                               or overloads[0].cpp_template is not None)
            if is_builtin_func:
                expr.builtin_module_call = module_name
                temp_call = TpyCall(func=TpyName(expr.method, loc=expr.loc), args=expr.args,
                                    kwargs=expr.kwargs,
                                    type_args=expr.type_args,
                                    type_args_parse_error=expr.type_args_parse_error,
                                    loc=expr.loc)
                if overloads[0].special_handling:
                    result = self.calls._analyze_special_builtin(temp_call, overloads)
                else:
                    result = self.calls._analyze_builtin_function_overloads(temp_call, overloads)
                expr.args = temp_call.args
                expr.kwargs = temp_call.kwargs
                expr.resolved_function_info = temp_call.resolved_function_info
                expr.inferred_type_args = temp_call.inferred_type_args
                expr.representational_subst_params = temp_call.representational_subst_params
                return result
            else:
                expr.user_module_call = module_name
                temp_call = TpyCall(func=TpyName(expr.method, loc=expr.loc), args=expr.args,
                                    kwargs=expr.kwargs,
                                    type_args=expr.type_args,
                                    type_args_parse_error=expr.type_args_parse_error,
                                    loc=expr.loc)
                result = self.calls._analyze_user_function_call(temp_call, overloads)
                expr.args = temp_call.args
                expr.kwargs = temp_call.kwargs
                expr.resolved_function_info = temp_call.resolved_function_info
                expr.inferred_type_args = temp_call.inferred_type_args
                expr.representational_subst_params = temp_call.representational_subst_params
                return result

        qname = f"{module_name}.{expr.method}"
        if record_info := self.ctx.registry.get_builtin_record(qname):
            if record_info.get_method_overloads("__init__") and not record_info.type_params:
                expr.builtin_module_call = module_name
                temp_call = TpyCall(func=TpyName(expr.method, loc=expr.loc), args=expr.args, kwargs=expr.kwargs, loc=expr.loc)
                result = self.calls._analyze_record_constructor(temp_call, record_info)
                expr.args = temp_call.args
                expr.kwargs = temp_call.kwargs
                expr.resolved_function_info = temp_call.resolved_function_info
                return result

        # User record constructor invoked via qualified access (e.g.
        # `module.RecordName(...)` after `from pkg import module`).
        # Generic records left out -- this path doesn't forward type_args,
        # so let them fall through to the existing generic handling below.
        if (module_info and module_info.records
                and expr.method in module_info.records
                and not module_info.records[expr.method].type_params):
            record_info = module_info.records[expr.method]
            expr.user_module_call = module_name
            temp_call = TpyCall(func=TpyName(expr.method, loc=expr.loc), args=expr.args, kwargs=expr.kwargs, loc=expr.loc)
            result = self.calls._analyze_record_constructor(temp_call, record_info)
            expr.args = temp_call.args
            expr.kwargs = temp_call.kwargs
            expr.resolved_function_info = temp_call.resolved_function_info
            return result

        # Check for call-site macro (e.g. dataclasses.asdict(...)).
        # Walks the re-export chain so macros re-exported through plain
        # modules (Phase 6) resolve to the macro registry's canonical
        # (ultimate_module, name) key.
        if self.ctx.macro_registry:
            macro_fn = self.ctx.macro_registry.get_call_macro(module_name, expr.method)
            ult_mod, ult_name = module_name, expr.method
            if macro_fn is None:
                chain = self.calls._resolve_call_macro_chain(module_name, expr.method)
                if chain is not None:
                    ult_mod, ult_name = chain
                    macro_fn = self.ctx.macro_registry.get_call_macro(ult_mod, ult_name)
            if macro_fn is not None:
                return self.calls._expand_call_macro_from_method(
                    expr, macro_fn, ult_mod, ult_name)

        raise self.ctx.error(f"Module '{module_name}' has no function '{expr.method}'", expr)

    def _try_resolve_dotted_module(self, obj: TpyFieldAccess) -> str | None:
        """Try to resolve nested field access as a dotted module name.

        Walks the TpyFieldAccess chain to collect segments (e.g.,
        a.b.c -> ["a", "b", "c"]), then checks the registry.
        Returns None if the base name is shadowed (bound as anything other
        than MODULE).
        """
        segments: list[str] = []
        current = obj
        while isinstance(current, TpyFieldAccess):
            segments.append(current.field)
            current = current.obj
        if not isinstance(current, TpyName):
            return None

        # Only resolve as module if base name is unbound or bound as MODULE
        if self.ctx.func.current_ns:
            binding = self.ctx.func.current_ns.lookup(current.name)
            if binding and binding.kind != BindingKind.MODULE:
                return None

        segments.append(current.name)
        segments.reverse()
        dotted_name = ".".join(segments)
        if self.ctx.registry.get_module(dotted_name):
            return dotted_name
        return None

    def _resolve_module_name(self, name: str) -> str | None:
        """Resolve a name to a module name if it refers to a module. Returns None otherwise."""
        if self.ctx.func.current_ns is None:
            return None
        binding = self.ctx.func.current_ns.lookup(name)
        if binding and binding.kind == BindingKind.MODULE:
            return binding.import_source[0] if binding.import_source else name
        return None

    # ------------------------------------------------------------------
    # Pending generic instance method calls (Phase 7a)
    # ------------------------------------------------------------------

    def _analyze_pending_generic_method_call(
        self, expr: TpyMethodCall, obj_type: PendingGenericInstanceType,
    ) -> TpyType:
        """Handle method call on a variable with unresolved generic type params.

        Accumulates type parameter constraints from method arguments.
        Eagerly resolves the generic instance once all type params are known.
        """
        info = self.ctx.func.pending_generic_instances.get(obj_type.instance_id)
        if info is None:
            raise self.ctx.error(
                f"Internal error: pending generic instance {obj_type.instance_id} not found", expr)

        record = info.record_info
        overloads = record.get_method_overloads(expr.method)
        if not overloads:
            raise self.ctx.error(
                f"'{record.name}' has no method '{expr.method}'", expr)

        # For MVP: use first overload (user records have single overloads per name)
        method = overloads[0]

        # Resolve kwargs (also enforces keyword-only constraints when no kwargs)
        if (expr.kwargs or method.has_keyword_only
                or method.materializes_defaults):
            expr.args = resolve_kwargs(
                expr.args, expr.kwargs, method.params, expr.method,
                lambda msg: self.ctx.error(msg, expr),
                call_loc=expr.loc, is_member=True,
            )
            expr.kwargs = {}

        # Check arity
        if len(expr.args) < method.min_args or len(expr.args) > method.max_args:
            raise self.ctx.error(
                arity_error_msg(expr.method, method.min_args, method.max_args, len(expr.args)),
                expr)

        # Analyze arguments and accumulate constraints. analyze_call_arg so a
        # `*xs` arg yields the unpacked element type rather than crashing the
        # structural analyzer; non-variadic reject gate handles validity.
        arg_types = [self.expr.analyze_call_arg(arg)
                     for arg in expr.args]
        type_param_names = set(info.type_params)
        for (pname, ptype), arg_type in zip(method.params, arg_types):
            if not contains_type_param(ptype, type_param_names):
                continue
            # Resolve IntLiteralType before binding
            resolved_arg = arg_type
            if isinstance(resolved_arg, IntLiteralType):
                resolved_arg = self.ctx.default_int_for_literal(resolved_arg)
            if not self.type_ops.match_type_with_inference(ptype, resolved_arg, info.inferred):
                # Check if this is a conflict with an existing binding
                for tp in info.type_params:
                    if tp in info.inferred:
                        existing = info.inferred[tp]
                        # Try matching just this param to see if it conflicts
                        test: dict[str, TpyType] = {}
                        self.type_ops.match_type_with_inference(ptype, resolved_arg, test)
                        if tp in test and test[tp] != existing:
                            raise self.ctx.error(
                                f"Conflicting type inference for '{tp}' in '{record.name}': "
                                f"previously inferred as '{existing}', "
                                f"but '{expr.method}' argument '{pname}' implies '{test[tp]}'",
                                expr,
                            )

        # Resolve IntLiteralType in any newly inferred params
        for k, v in list(info.inferred.items()):
            if isinstance(v, IntLiteralType):
                info.inferred[k] = self.ctx.default_int_for_literal(v)

        # Check if all type params are now resolved
        all_resolved = all(tp in info.inferred for tp in info.type_params)

        if all_resolved:
            resolved_type = self._eagerly_resolve_pending_generic(info)
            # Re-dispatch: analyze the method call on the now-concrete type
            self.ctx.set_expr_type(expr.obj, resolved_type)
            if isinstance(expr.obj, TpyName) and self.ctx.func.current_scope:
                self.ctx.func.current_scope.define(expr.obj.name, resolved_type)
            result = self._try_resolve_method(expr, resolved_type)
            if result is None:
                raise self.ctx.error(
                    f"'{resolved_type}' has no method '{expr.method}'", expr)
            return result

        # Not fully resolved yet -- check return type
        return_type = method.return_type
        if contains_type_param(return_type, type_param_names):
            # Check if we can substitute what we have so far
            unresolved_in_return = _unresolved_params_in_type(return_type, info.inferred, type_param_names)
            if unresolved_in_return:
                raise self.ctx.error(
                    f"Cannot determine return type of '{expr.method}' on '{record.name}': "
                    f"type parameter{'s' if len(unresolved_in_return) > 1 else ''} "
                    f"{', '.join(unresolved_in_return)} not yet resolved; "
                    f"call a constraining method first or add explicit type arguments",
                    expr,
                )
            # All params in return type are resolved, substitute
            return_type = self.type_ops.substitute_type_params(return_type, info.inferred)

        # Set minimal function info for void methods
        expr.resolved_function_info = FunctionInfo(
            name=expr.method,
            params=method.params,
            return_type=return_type,
            # Resumable-factory flags drive call-site capture decisions
            # (e.g. temporary-receiver lift); a pending-generic generator/
            # async method must not lose them on this minimal path. The
            # declared-return shape rides along for the same reason: losing
            # it would classify a borrow-returning coro as owned and skip
            # the erasure-boundary reject.
            is_async=method.is_async,
            is_generator=method.is_generator,
            async_inner_return=method.async_inner_return,
            # Method-kind bits ride along too: call-site consumers classify
            # the callee off this fi, and losing them here misfiles a plain
            # instance method as a free function.
            is_method=method.is_method,
            is_staticmethod=method.is_staticmethod,
            is_classmethod=method.is_classmethod,
            canonical_fi=method.root,
        )
        return return_type

    def try_resolve_pending_from_expected_type(
        self, pending: PendingGenericInstanceType, expected: TpyType,
        loc: 'SourceLocation | None' = None,
    ) -> NominalType | None:
        """Try to resolve a pending generic instance from an expected type.

        Used when a pending-type variable is passed to a typed parameter or
        returned where the function return type is known. Returns the resolved
        concrete type, or None if the expected type doesn't match.
        """
        info = self.ctx.func.pending_generic_instances.get(pending.instance_id)
        if info is None:
            return None

        # Unwrap Own/Optional/Readonly/Ref to find the inner NominalType
        target = expected
        if isinstance(target, OwnType):
            target = target.wrapped
        if isinstance(target, OptionalType):
            target = target.inner
        target = unwrap_readonly(unwrap_ref_type(target))

        if not isinstance(target, NominalType) or target.name != info.record_name:
            return None
        if not target.type_args or len(target.type_args) != len(info.type_params):
            return None

        # Build pattern with TypeParamRefs for unresolved params
        pattern_args = []
        for tp in info.type_params:
            if tp in info.inferred:
                pattern_args.append(info.inferred[tp])
            else:
                pattern_args.append(TypeParamRef(tp))
        pattern = NominalType(
            info.record_name, tuple(pattern_args),
            _module_qname=info.record_info.qualified_name(),
        )

        # Match to extract constraints
        if not self.type_ops.match_type_with_inference(pattern, target, info.inferred):
            # Check if a previously-inferred param conflicts with the expected type
            for tp, expected_arg in zip(info.type_params, target.type_args):
                if tp in info.inferred and isinstance(expected_arg, TpyType):
                    if info.inferred[tp] != expected_arg:
                        raise SemanticError(
                            f"Conflicting type for '{tp}' in '{info.record_name}': "
                            f"previously inferred as '{info.inferred[tp]}', "
                            f"but expected type requires '{expected_arg}'",
                            loc,
                        )
            return None

        # Resolve IntLiteralType in any newly inferred params
        for k, v in list(info.inferred.items()):
            if isinstance(v, IntLiteralType):
                info.inferred[k] = self.ctx.default_int_for_literal(v)

        # Check if all type params are now resolved
        if not all(tp in info.inferred for tp in info.type_params):
            return None

        return self._eagerly_resolve_pending_generic(info)

    def _eagerly_resolve_pending_generic(self, info: 'PendingGenericInstanceInfo') -> NominalType:
        """Resolve a pending generic instance to a concrete NominalType."""
        type_args = tuple(info.inferred[tp] for tp in info.type_params)
        resolved_type = NominalType(
            info.record_name, type_args,
            _module_qname=info.record_info.qualified_name(),
        )

        # Validate type param bounds
        for param_name, type_arg in zip(info.type_params, type_args):
            if param_name in info.record_info.type_param_bounds:
                bound = info.record_info.type_param_bounds[param_name]
                if not self.protocols.satisfies_bound(type_arg, bound):
                    raise self.ctx.error(
                        f"Inferred type '{type_arg}' does not satisfy bound '{bound}' "
                        f"for type parameter '{param_name}' of '{info.record_name}'",
                        info.expr,
                    )

        # Update constructor expression
        info.expr.call_type = resolved_type
        self.ctx.set_expr_type(info.expr, resolved_type)

        # Update scope and var_types (only when bound to a local variable,
        # not for inline expressions like Container().set(...) which would
        # corrupt the class binding in scope)
        if info.decl_line is not None:
            if self.ctx.func.current_scope:
                self.ctx.func.current_scope.define(info.variable_name, resolved_type)
            if self.ctx.func.current_ns:
                self.ctx.func.current_ns.bind_variable(info.variable_name, resolved_type)
            var_decl = self.ctx.func.var_decl_by_name.get(info.variable_name)
            if var_decl:
                self.ctx.var_types[var_decl] = resolved_type
            self.ctx.declared_var_types[(info.decl_line, info.variable_name)] = resolved_type

        # Set constructor info now that we have concrete types
        type_subst = info.inferred
        self.calls._set_record_constructor_info(info.expr, info.record_info, resolved_type, type_subst)

        # Clean up tracking
        del self.ctx.func.pending_generic_instances[info.instance_id]
        self.ctx.func.variable_to_generic_instance.pop(info.variable_name, None)

        return resolved_type

    def _analyze_typed_dict_get(self, expr: TpyMethodCall, record_info, obj_type: TpyType) -> TpyType:
        """Analyze td.get("key") or td.get("key", default) on a TypedDict."""
        if len(expr.args) < 1 or len(expr.args) > 2:
            raise self.ctx.error(
                f"TypedDict.get() takes 1 or 2 arguments, got {len(expr.args)}", expr)
        if expr.kwargs:
            raise self.ctx.error("TypedDict.get() does not accept keyword arguments", expr)
        key_expr = expr.args[0]
        if not isinstance(key_expr, TpyStrLiteral):
            raise self.ctx.error(
                f"TypedDict '{obj_type.name}' keys must be string literals", key_expr)
        key = key_expr.value
        type_subst = self.type_ops.build_type_substitution(obj_type)
        for fld in record_info.fields:
            if fld.name == key:
                field_type = fld.type
                if type_subst:
                    field_type = self.type_ops.substitute_type_params(field_type, type_subst)
                # total=False: field stored as Optional[T], unwrap to get inner type
                # total=True: field is always present, even if annotated Optional[T]
                is_absent_optional = record_info.is_total_false
                if is_absent_optional:
                    assert isinstance(field_type, OptionalType)
                    inner_type = field_type.inner
                else:
                    inner_type = field_type
                expr.typed_dict_get_field = key
                expr.typed_dict_get_optional = is_absent_optional
                # Analyze key expression so its type is recorded
                self.expr.analyze_expr(key_expr)
                has_default = len(expr.args) == 2
                if has_default:
                    default_type = self.expr.analyze_expr_with_hint(
                        expr.args[1], inner_type)
                    self.compat.check_type_compatible(
                        default_type, inner_type,
                        f"default value for TypedDict.get()", loc=expr.args[1].loc,
                        source_expr=expr.args[1])
                    return inner_type
                else:
                    # Don't double-wrap: total=True Optional[T] field is already Optional
                    if isinstance(inner_type, OptionalType):
                        return inner_type
                    return OptionalType(inner_type)
        raise self.ctx.error(
            f"TypedDict '{obj_type.name}' has no key '{key}'", key_expr)

    def _analyze_instance_method(self, expr: TpyMethodCall, obj_type: TpyType,
                                  is_readonly_receiver: bool = False,
                                  is_consuming_receiver: bool = False) -> TpyType | None:
        """Analyze instance method call on any type (builtin or user record)."""
        record_info = self.ctx.registry.receiver_record(obj_type)
        if not record_info:
            return None
        # TypedDict: td.get("key") / td.get("key", default)
        if record_info.is_typed_dict and expr.method == "get":
            return self._analyze_typed_dict_get(expr, record_info, obj_type)
        overloads, inherited_subst = self.protocols.lookup_record_method_overloads(
            record_info, expr.method)
        if not overloads:
            return None
        if (record_info.is_return_exception
                and expr.method in qnames.THROWABLE_ABI_METHODS
                and not record_info.get_method_overloads(expr.method)):
            raise self.ctx.error(
                f"'{record_info.name}' is a return-only exception "
                f"(ReturnException): it is a plain value that is never thrown, "
                f"so it has no '{expr.method}()'", expr)
        self._reject_inherited_classmethod(expr, record_info, overloads)
        instance_subst = self.type_ops.build_type_substitution(obj_type)
        if inherited_subst and instance_subst:
            # An `N: int` param's binding is a plain int -- nothing to
            # substitute (and substitute_type_params would crash on it).
            type_subst = {
                k: (self.type_ops.substitute_type_params(v, instance_subst)
                    if isinstance(v, TpyType) else v)
                for k, v in inherited_subst.items()
            }
        elif inherited_subst:
            type_subst = inherited_subst
        else:
            type_subst = instance_subst

        method_info = overloads[0]

        # @inline: clone body and substitute at call site.
        # For FStr params, validate that f-string literals are passed.
        if method_info.inline_body is not None:
            for i, (pi, arg) in enumerate(zip(method_info.params, expr.args)):
                if is_fstr_type(pi.type) and not isinstance(arg, TpyFString):
                    if isinstance(arg, TpyStrLiteral):
                        expr.args[i] = TpyFString(parts=[arg.value], loc=arg.loc)
                    else:
                        raise self.ctx.error(
                            f"Parameter '{pi.name}' has type FStr -- only f-string "
                            f"or string literals are accepted", arg)
            return self._inline_method_call(expr, method_info)

        # Check if the method has its own type parameters (generic method).
        # Generic methods don't support multiple overloads; user-defined methods
        # always register a single overload per name (registration.py).
        if method_info.is_generic():
            if len(overloads) > 1:
                raise self.ctx.error(
                    f"Overloaded generic methods are not supported for '{expr.method}'", expr)
            result = self._analyze_generic_method_call(
                expr, method_info, record_info, type_subst)
            self._warn_copy_returns(expr, result)
            return result

        result = self._resolve_and_check_args(
            expr, overloads, type_subst, is_readonly_receiver=is_readonly_receiver,
            is_consuming_receiver=is_consuming_receiver)
        self._warn_copy_returns(expr, result)
        return result

    def _warn_copy_returns(self, expr: TpyMethodCall,
                           result: TpyType) -> None:
        """Acknowledged CPython divergence for `@copy_returns_warn` accessors
        (e.g. two-arg `dict.get`): the `Own[V]` result is a copy where the
        method's CPython namesake aliases, so mutating it is a silent no-op.
        copy() is the acknowledgment spelling; value-type results are
        parity-clean (immutable in CPython). The fact is library-declared on
        the stub, not keyed on any C++ symbol here."""
        fi = expr.resolved_function_info
        if (fi is None or not fi.copy_returns_warn
                or self.ctx.func.in_copy_call_arg):
            return
        bare = result.wrapped if isinstance(result, OwnType) else result
        if unwrap_readonly(bare).is_value_type():
            return
        self.ctx.warning(
            f"'{expr.method}(...)' returns a copy of the stored value; "
            "mutations through it do not affect the container (CPython "
            "aliases). Use an aliasing accessor (e.g. subscript), or "
            "wrap in copy() to make the copy explicit.", expr)

    def _inline_method_call(
        self, expr: TpyMethodCall, method_info: FunctionInfo,
    ) -> TpyType:
        """Inline a method with FStr parameter at the call site.

        Clones the method's body expression, substitutes ``self`` with the
        receiver and the FStr parameter with the actual f-string argument,
        then analyzes the substituted expression. This lets the f-string
        literal reach the call macro for decomposition.
        """
        body = copy.deepcopy(method_info.inline_body)

        # Build the substitution map: param name -> call-site argument.
        # method_info.params does NOT include 'self' (it's implicit for methods).
        param_map: dict[str, TpyExpr] = {}
        for pi, arg in zip(method_info.params, expr.args):
            param_map[pi.name] = arg

        # Substitute names in the cloned body
        MethodAnalyzer._substitute_inline_body(body, expr.obj, param_map)

        # Store the inlined expression on the method call node for codegen
        expr.fstr_expansion = body

        # Analyze the substituted expression (side effect: type-checks the macro expansion)
        self.expr.analyze_expr(body)
        return VOID

    @staticmethod
    def _substitute_inline_body(
        node: TpyExpr, receiver: TpyExpr | None,
        param_map: dict[str, TpyExpr],
    ) -> None:
        """In-place substitute names in a cloned @inline body expression.

        Args:
            node: The cloned body expression to substitute in.
            receiver: The ``self`` replacement (method calls), or None (free functions).
            param_map: Parameter name -> call-site argument expression.

        Replaces TpyName references matching param_map keys or "self" (when
        receiver is provided) in positional args, function refs, and method
        receivers.

        Current limitation: only handles TpyCall, TpyMethodCall, TpyFieldAccess,
        and TpyName in positional args. Does not recurse into kwargs, TpyBinOp,
        TpyIfExpr, TpySubscript, or other nested expression types. Sufficient
        for @inline bodies constrained to a single call. Future: support
        multi-statement bodies via expression blocks.
        """
        sub = MethodAnalyzer._substitute_inline_body
        sub_args = MethodAnalyzer._substitute_inline_args
        if isinstance(node, TpyCall):
            if isinstance(node.func, TpyName) and node.func.name in param_map:
                node.func = param_map[node.func.name]
            sub_args(node.args, receiver, param_map)
        elif isinstance(node, TpyMethodCall):
            if receiver and isinstance(node.obj, TpyName) and node.obj.name == "self":
                node.obj = receiver
            sub_args(node.args, receiver, param_map)

    @staticmethod
    def _substitute_inline_args(
        args: list[TpyExpr], receiver: TpyExpr | None,
        param_map: dict[str, TpyExpr],
    ) -> None:
        """Substitute names in a list of positional arguments."""
        sub = MethodAnalyzer._substitute_inline_body
        for i, arg in enumerate(args):
            if isinstance(arg, TpyName) and arg.name in param_map:
                args[i] = param_map[arg.name]
            elif receiver and isinstance(arg, TpyName) and arg.name == "self":
                args[i] = receiver
            elif isinstance(arg, TpyFieldAccess):
                if receiver and isinstance(arg.obj, TpyName) and arg.obj.name == "self":
                    arg.obj = receiver
                else:
                    sub(arg, receiver, param_map)
            elif isinstance(arg, (TpyCall, TpyMethodCall)):
                sub(arg, receiver, param_map)

    def _analyze_generic_method_call(
        self, expr: TpyMethodCall, method_info: FunctionInfo,
        record_info, class_subst: dict[str, TpyType | int],
    ) -> TpyType:
        """Analyze a call to a generic method (method with its own type parameters).

        Handles two kinds of method type params:
        - New params: type params not in the class (e.g. U on def transform[U])
        - Constrained class params: class type params with an additional method-level bound
        """
        # Classify against every param the receiver's instantiation binds, not
        # just the receiver record's own params: an inherited method's class
        # params live on the DECLARING base and reach here only as keys of the
        # composed class_subst, so a shadowed `def m[T: Bound]` called through
        # a subclass must classify as constrained, not as a fresh param.
        class_type_params = set(record_info.type_params or ()) | set(class_subst)
        new_params = [tp for tp in method_info.type_params if tp not in class_type_params]
        constrained_class_params = [tp for tp in method_info.type_params if tp in class_type_params]

        # Arity-check explicit type args before the no-new-params early return,
        # so a fully-shadowed method loudly rejects `.m[X](...)` instead of
        # silently discarding X (the args would otherwise be name-captured by
        # class_subst, never by the explicit spelling).
        if expr.type_args and len(expr.type_args) != len(new_params):
            raise self.ctx.error(
                f"Method '{method_info.name}' expects {len(new_params)} type argument(s), "
                f"got {len(expr.type_args)}",
                expr)

        # Validate per-method bounds on class type params: check that the concrete
        # class type satisfies the method's bound. These are class-level type params,
        # so they must be in class_subst for any fully-instantiated generic class.
        for tp in constrained_class_params:
            if tp not in class_subst:
                raise self.ctx.error(
                    f"Method '{method_info.name}' has bound on class type parameter '{tp}', "
                    f"but the class is not instantiated with a concrete type for '{tp}'",
                    expr)
        raise_if_class_param_bound_violated(
            method_info, class_type_params, class_subst,
            self.protocols.type_conforms_to_protocol,
            self.ctx.error, expr,
        )

        if not new_params:
            # All method type params are constrained class params -- no inference needed.
            # Single-element list: generic methods can't have multiple overloads (guarded above).
            return self._resolve_and_check_args(expr, [method_info], class_subst)

        # Build a partial FunctionInfo with only new params for inference
        partial_func = FunctionInfo(
            name=method_info.name,
            params=method_info.params,
            return_type=method_info.return_type,
            type_params=new_params,
            type_param_bounds={k: v for k, v in method_info.type_param_bounds.items()
                               if k in new_params},
            canonical_fi=method_info.root,
        )

        # Pre-substitute class params in the method signature so inference
        # only needs to resolve new params
        if class_subst:
            partial_func = self.type_ops.substitute_method_type_params(partial_func, class_subst)

        # Infer new params from arguments or explicit type args. The LHS-hint
        # seed is only useful on paths that actually re-analyze args with a
        # contextual hint, so it (and the closure that consumes it) lives
        # inside the inference branches that need it.
        has_wildcards = expr.type_args and None in expr.type_args
        if expr.type_args:
            # Arity already validated before the no-new-params early return.
            if not has_wildcards:
                # Full explicit -- no seed needed.
                method_subst = dict(zip(new_params, expr.type_args))
            else:
                # Partial explicit -- seed + explicit positional args drive
                # the per-arg hint; wildcards leave the seed binding in place.
                method_subst = self._infer_method_subst_with_seed(
                    expr, partial_func, method_info, new_params,
                    explicit_type_args=expr.type_args,
                )
        else:
            method_subst = self._infer_method_subst_with_seed(
                expr, partial_func, method_info, new_params,
                explicit_type_args=None,
            )

        # Merge class subst + method subst up front: a method-level bound may
        # name a class-level type param (`class C[R]: def m[T: Proto[R]]`), so
        # bound validation below must substitute with the full map, not just the
        # method-level one (else the class param is unresolved when substituting).
        full_subst = dict(class_subst) if class_subst else {}
        full_subst.update(method_subst)

        # Validate bounds for new params (inference checks bounds internally,
        # but explicit type args bypass inference)
        new_param_bounds = {k: v for k, v in method_info.type_param_bounds.items()
                           if k in set(new_params)}
        if new_param_bounds:
            validate_type_param_bounds(
                full_subst, new_param_bounds, method_info.name,
                self.protocols.satisfies_bound,
                lambda msg: self.ctx.error(msg, expr),
                self.type_ops.substitute_type_params,
            )

        # Store inferred type args (new params only) for codegen
        expr.inferred_type_args = tuple(
            resolve_inferred_type_arg(method_subst[p], self.ctx.default_int_type)
            for p in new_params
        )
        expr.representational_subst_params = (
            self.type_ops.compute_representational_subst_params(
                method_info, expr.inferred_type_args))

        return self._resolve_and_check_args(expr, [method_info], full_subst)

    def _infer_method_subst_with_seed(
        self,
        expr: TpyMethodCall,
        partial_func: FunctionInfo,
        method_info: FunctionInfo,
        new_params: list[str],
        explicit_type_args: tuple['TpyType | None', ...] | None,
    ) -> dict[str, TpyType]:
        """Analyze args with an LHS-hint seed and run method-param inference.

        Shared by the pure-inference and partial-explicit (wildcard) branches
        of ``_analyze_generic_method_call``. The seed is computed against the
        already class-substituted ``partial_func.return_type``; explicit
        positional type args (when present) overlay the seed at their
        positions.
        """
        merged_seed = self.type_ops.seed_subst_from_return_hint(
            partial_func, self.ctx.slot_hint_at(expr),
        ).with_explicit(new_params, explicit_type_args)

        # Default-arg capture freezes ``partial_func`` and ``merged_seed`` at
        # def-time so this closure isn't sensitive to later rebinding.
        def _analyze_args(
            _pf: FunctionInfo = partial_func,
            _seed: ReturnSeed = merged_seed,
        ) -> list[TpyType]:
            return [self.expr.analyze_call_arg(
                        arg, seeded_arg_hint(_pf.params, i, _seed))
                    for i, arg in enumerate(expr.args)]

        arg_types = _analyze_args()
        method_subst = self.type_ops.infer_type_params_for_function(
            partial_func, arg_types, self.protocols.satisfies_bound,
            expected_return_type=self.ctx.slot_hint_at(expr),
            explicit_type_args=explicit_type_args,
        )
        if method_subst is None:
            raise self.ctx.error(
                f"Cannot infer type arguments for method '{method_info.name}'. "
                f"Specify explicitly: .{method_info.name}[{', '.join(new_params)}](...)",
                expr)
        return method_subst

    def _is_protocol_method_readonly(self, protocol_name: str, method_name: str) -> bool:
        """Check if a protocol method is readonly (per-method or protocol-level).

        Searches inherited methods too, so a @readonly method from a parent
        protocol is correctly recognized.
        """
        proto_info = self.ctx.registry.scan_by_short_name(protocol_name)
        if proto_info is None:
            return False
        if proto_info.is_readonly:
            return True
        for msig in self.protocols.collect_protocol_methods(protocol_name):
            if msig.name == method_name:
                return msig.is_readonly
        return False

    def _build_protocol_method_info(self, protocol_name: str, method_name: str,
                                     raw_params: list[tuple[str, TpyType]],
                                     return_type: TpyType,
                                     cpp_template: str | None = None,
                                     param_defaults: list | None = None,
                                     num_posonly: int = 0) -> FunctionInfo:
        """Build a FunctionInfo for a protocol method signature."""
        # param_defaults is parallel to raw_params (None for required, TpyExpr for optional);
        # empty list means no defaults declared at protocol method level.
        defaults = param_defaults if param_defaults else [None] * len(raw_params)
        params = [
            ParamInfo(n, t, default_expr=default,
                      positional_only=i < num_posonly)
            for i, ((n, t), default) in enumerate(zip(raw_params, defaults))
        ]
        # __next__ on Iterator protocol has implicit @error_return(StopIteration)
        error_return_type = None
        if method_name == "__next__":
            error_return_type = "builtins.StopIteration"
        return FunctionInfo(
            name=method_name, params=params, return_type=return_type,
            is_method=True,
            is_readonly=self._is_protocol_method_readonly(protocol_name, method_name),
            cpp_template=cpp_template,
            error_return_type=error_return_type,
        )

    def _analyze_protocol_or_bound_method(self, expr: TpyMethodCall, obj_type: TpyType) -> TpyType | None:
        """Analyze method calls on protocol-typed values or bounded type parameters."""
        if is_protocol_type(obj_type):
            method_sig = self.protocols.get_protocol_method_signature(obj_type, expr.method)
            if method_sig is None:
                raise self.ctx.error(f"Protocol '{obj_type.name}' has no method '{expr.method}'", expr)
            raw_params, return_type, cpp_template, param_defaults, n_posonly = method_sig
            fi = self._build_protocol_method_info(
                obj_type.name, expr.method, raw_params, return_type,
                cpp_template, param_defaults, n_posonly)
            return self._resolve_and_check_args(expr, [fi], {})

        if isinstance(obj_type, TypeParamRef):
            bound = self.type_ops.get_type_param_bound(obj_type.name)
            if bound is not None and is_protocol_type(bound):
                method_sig = self.protocols.get_protocol_method_signature(bound, expr.method, self_type=obj_type)
                if method_sig is None:
                    raise self.ctx.error(f"Protocol '{bound.name}' has no method '{expr.method}'", expr)
                raw_params, return_type, cpp_template, param_defaults, n_posonly = method_sig
                fi = self._build_protocol_method_info(
                    bound.name, expr.method, raw_params, return_type,
                    cpp_template, param_defaults, n_posonly)
                return self._resolve_and_check_args(expr, [fi], {})

        return None

    def _analyze_super_method_call(self, expr: TpyMethodCall, super_type: SuperType) -> TpyType:
        """Analyze a super().method() call.

        The method is looked up in the parent class and type arguments are
        substituted for generic parent classes. `super_type` is produced by
        analyze_expr on the receiver (see calls.py qname dispatch on
        `builtins.super`).

        Multi-base (D22 v2.3): __del__ is rejected outright; other methods are
        resolved by walking the child's C3 MRO and picking the first ancestor
        whose own method table defines the name. See _resolve_super_parent_type.
        """
        parent_type = self._resolve_super_parent_type(expr, super_type)
        parent_info = self.ctx.registry.get_record_for_type(parent_type)
        if parent_info is None:
            raise self.ctx.error(f"Parent class '{parent_type}' not found", expr)

        # Special handling for super().__init__()
        if expr.method == "__init__":
            # super().__init__() can only be called inside __init__
            if self.ctx.func.current_function is None or self.ctx.func.current_function.name != "__init__":
                raise self.ctx.error(
                    "super().__init__() can only be called inside __init__",
                    expr
                )
            # Check for duplicate super().__init__() calls
            if self.ctx.func.super_init_call is not None:
                raise self.ctx.error(
                    "super().__init__() can only be called once",
                    expr
                )
            # Track this call for later validation (must be first statement)
            self.ctx.func.super_init_call = expr

            child_rec = self.ctx.registry.get_record(super_type.child_record_name)
            if (child_rec is not None
                    and not self.ctx.registry.is_struct_base(child_rec, parent_info)):
                # The C++ struct derives from the empty value base, not from
                # the thrown exception `super()` names, so there is no
                # Exception(message) constructor behind this call.
                if expr.args:
                    raise self.ctx.error(
                        f"'{child_rec.name}' is a return-only exception "
                        f"(ReturnException): it carries only the fields it "
                        f"declares and has no Exception(message) constructor to "
                        f"call; store the message in a declared 'message: str' "
                        f"field instead", expr)
                expr.super_parent_type = parent_type
                return VOID

            init_overloads = parent_info.get_method_overloads("__init__")
            if not init_overloads:
                # Parent has no __init__, allow with no arguments
                if expr.args:
                    raise self.ctx.error(
                        f"Parent class '{parent_type}' has no __init__, "
                        "super().__init__() must be called with no arguments",
                        expr
                    )
                # Store parent type for codegen (will generate default base init)
                expr.super_parent_type = parent_type
                return VOID

        # Special handling for super().__del__()
        if expr.method == "__del__":
            # super().__del__() can only be called inside __del__
            if self.ctx.func.current_function is None or self.ctx.func.current_function.name != "__del__":
                raise self.ctx.error(
                    "super().__del__() can only be called inside __del__",
                    expr
                )
            # Check for duplicate super().__del__() calls
            if self.ctx.func.super_del_call is not None:
                raise self.ctx.error(
                    "super().__del__() can only be called once",
                    expr
                )
            # Track this call for later validation (must be last statement)
            self.ctx.func.super_del_call = expr
            # No arguments allowed
            if expr.args:
                raise self.ctx.error(
                    "super().__del__() takes no arguments",
                    expr
                )
            expr.super_parent_type = parent_type
            return VOID

        # Check readonly constraint: super() in @readonly method inherits readonly
        is_readonly_context = (
            isinstance(self.ctx.func.current_function, TpyFunction)
            and self.ctx.func.current_function.is_readonly
        )

        # Look up the method in the parent class.
        # For __init__, we already have init_overloads; for other methods, walk
        # the parent's MRO so inherited methods (not defined on parent_info itself)
        # still resolve -- fixes super().foo() when foo lives on parent's ancestor.
        if expr.method == "__init__":
            overloads = init_overloads
        else:
            overloads = self.ctx.registry.get_method_overloads_with_parents(
                parent_info, expr.method
            )
        if not overloads:
            raise self.ctx.error(
                f"Parent class '{parent_type}' has no method '{expr.method}'",
                expr
            )

        # Build type substitution for generic parent (e.g., Container[int32] -> {"T": int32})
        type_subst = self.protocols.get_parent_type_subst(parent_type, parent_info)

        # Must be set before arg resolution: mutation-call-edge recording
        # treats super() receivers as self for self-mutation propagation.
        expr.super_parent_type = parent_type

        # Check if the method has its own type parameters (generic method)
        method_info = overloads[0]
        if method_info.is_generic():
            if len(overloads) > 1:
                raise self.ctx.error(
                    f"Overloaded generic methods are not supported for '{expr.method}'", expr)
            return_type = self._analyze_generic_method_call(
                expr, method_info, parent_info, type_subst)
        else:
            return_type = self._resolve_and_check_args(expr, overloads, type_subst)
        if is_readonly_context and not expr.resolved_function_info.is_readonly:
            raise self.ctx.error(
                f"Cannot call non-readonly method '{expr.method}' on readonly reference",
                expr)
        return return_type

    @staticmethod
    def stmt_contains_super_init(stmt: TpyStmt, super_init: TpyMethodCall) -> bool:
        """Check if a statement contains the given super().__init__() call.

        Used to validate that super().__init__() is the first statement.
        """
        # Direct expression statement containing the super().__init__() call
        if isinstance(stmt, TpyExprStmt):
            return stmt.expr is super_init
        return False

    @staticmethod
    def find_first_non_docstring_stmt(stmts: list[TpyStmt]) -> TpyStmt | None:
        """Find the first non-docstring statement in a list.

        Returns None if all statements are docstrings or list is empty.
        """
        for stmt in stmts:
            if is_docstring(stmt):
                continue
            return stmt
        return None

    @staticmethod
    def find_last_non_docstring_stmt(stmts: list[TpyStmt]) -> TpyStmt | None:
        """Find the last statement, skipping only a leading docstring.

        Only the first statement can be a docstring; trailing string literals
        are regular statements, not docstrings.
        """
        if not stmts:
            return None
        if len(stmts) == 1 and is_docstring(stmts[0]):
            return None
        return stmts[-1]

