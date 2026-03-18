"""
TurboPython Method Analysis

Method call and super() analysis.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, OwnType, OptionalType, ListType, PendingListType, PendingDictType, PendingSetType,
    DictType, SetType,
    SuperType, TypeParamRef, FunctionInfo, ParamInfo, VOID, is_protocol_type,
    PtrType, ReadonlyType, unwrap_readonly, UnknownElementType,
    PendingGenericInstanceType, IntLiteralType,
)
from ..parse import (
    TpyCall, TpyMethodCall, TpyName, TpyFieldAccess, TpyFunction, TpyExprStmt, TpyStrLiteral, TpyStmt,
    is_super_del_call,
)
from ..namespace import BindingKind
from ..coercions import CoercionContext
from ..prescan import _expr_to_narrowing_key
from .diagnostics import OPTIONAL_NONE_ACCESS_WARNING
from .overloads import resolve_overload
from .calls import (
    arity_error_msg, resolve_kwargs, validate_generic_defaults,
    validate_type_param_bounds, prefer_strview_for_literals,
)

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker
    from .compatibility import TypeCompatibility
    from .local_deduction import LocalTypeDeduction
    from .expressions import ExpressionAnalyzer
    from .calls import CallAnalyzer
    from ..typesys import PendingGenericInstanceInfo

from tpyc.modules.builtins import LIST_MUTATION_METHODS, LIST_ITER_INVALIDATING, DICT_MUTATION_METHODS, SET_MUTATION_METHODS
from .context import _storage_key


def _contains_type_param_ref_type(typ: TpyType, param_names: set[str]) -> bool:
    """Check if a type contains any TypeParamRef matching the given param names."""
    if isinstance(typ, TypeParamRef):
        return typ.name in param_names
    if isinstance(typ, NamedType) and typ.type_args:
        return any(
            _contains_type_param_ref_type(a, param_names)
            for a in typ.type_args if isinstance(a, TpyType)
        )
    # Check common wrapper types
    for attr in ('element_type', 'pointee', 'inner', 'wrapped'):
        inner = getattr(typ, attr, None)
        if inner is not None and isinstance(inner, TpyType):
            if _contains_type_param_ref_type(inner, param_names):
                return True
    # TupleType
    if hasattr(typ, 'element_types'):
        return any(_contains_type_param_ref_type(e, param_names) for e in typ.element_types)
    # DictType
    if hasattr(typ, 'key_type') and hasattr(typ, 'value_type'):
        return (_contains_type_param_ref_type(typ.key_type, param_names)
                or _contains_type_param_ref_type(typ.value_type, param_names))
    # UnionType
    if hasattr(typ, 'members'):
        return any(_contains_type_param_ref_type(m, param_names) for m in typ.members)
    return False


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
    if isinstance(typ, NamedType) and typ.type_args:
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
        # Set via set_cross_deps() to break circular dependency
        self.expr: ExpressionAnalyzer | None = None
        self.calls: CallAnalyzer | None = None

    def set_cross_deps(self, expr: ExpressionAnalyzer, calls: CallAnalyzer) -> None:
        """Wire circular dependencies (must be called before analyze_method_call)."""
        self.expr = expr
        self.calls = calls

    def _check_and_coerce_args(
        self, expr: TpyMethodCall,
        params: list[tuple[str, TpyType]],
        arg_types: list[TpyType] | None = None,
    ) -> None:
        """Analyze, ownership-check, and coerce method arguments in place.

        If arg_types is None, each arg is analyzed with a type hint from the
        corresponding param (using pre-analyzed types from empty list inference
        when available). Otherwise pre-analyzed arg_types are used.
        """
        pre = self.ctx.pre_analyzed_method_args.pop(id(expr), None)
        for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, params)):
            if arg_types is not None:
                at = arg_types[i]
            elif pre is not None and i < len(pre):
                at = pre[i]
            else:
                at = self.expr.analyze_expr_with_hint(arg, ptype)
            self.calls.check_own_param(arg, at, pname, ptype)
            expr.args[i] = self.compat.coerce_expr(arg, at, ptype, f"argument '{pname}'",
                                                    coercion_ctx=CoercionContext.ARG)

    def _resolve_and_check_args(
        self, expr: TpyMethodCall,
        overloads: list[FunctionInfo],
        type_subst: dict[str, TpyType | int],
    ) -> TpyType:
        """Resolve overloads, substitute type params, check arg count, and coerce args.

        Sets expr.resolved_function_info. Returns the return type.
        """
        # Resolve kwargs before arity check
        if expr.kwargs:
            if len(overloads) > 1:
                raise self.ctx.error(
                    f"Keyword arguments not supported for overloaded method '{expr.method}'", expr)
            target = overloads[0]
            resolved_target = (self.type_ops.substitute_method_type_params(target, type_subst)
                               if type_subst else target)
            expr.args = resolve_kwargs(
                expr.args, expr.kwargs, resolved_target.params, expr.method,
                lambda msg: self.ctx.error(msg, expr),
                call_loc=expr.loc,
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
            self._check_and_coerce_args(expr, resolved.params)
            if type_subst:
                validate_generic_defaults(
                    expr.args, unresolved, type_subst, self.type_ops,
                    lambda msg: self.ctx.error(msg, expr))
        else:
            arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
            resolved_overloads = [
                self.type_ops.substitute_method_type_params(m, type_subst) if type_subst else m
                for m in overloads
            ]
            resolved = resolve_overload(
                resolved_overloads, arg_types,
                protocol_checker=self.protocols.type_conforms_to_protocol,
                deref_checker=self.type_ops.get_deref_coercion_target,
                default_int_type=self.ctx.default_int_type,
                subclass_checker=self.ctx.registry.is_subclass_of,
            )
            if resolved is None:
                arg_strs = ", ".join(str(t) for t in arg_types)
                raise self.ctx.error(
                    f"No matching overload for '{expr.method}' with argument types ({arg_strs})", expr)
            expr.resolved_function_info = resolved
            self._check_and_coerce_args(expr, resolved.params, arg_types)
            # Overloaded methods with generic defaults: find unresolved counterpart
            if type_subst:
                idx = resolved_overloads.index(resolved)
                validate_generic_defaults(
                    expr.args, overloads[idx], type_subst, self.type_ops,
                    lambda msg: self.ctx.error(msg, expr))

        if self.calls is not None:
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
        if ctx.current_function is None or not isinstance(ctx.current_function, TpyFunction):
            raise ctx.error("super() can only be used inside a method", expr)

        if not ctx.current_function.is_method:
            raise ctx.error("super() can only be used inside a method", expr)

        if ctx.current_function.is_staticmethod:
            raise ctx.error("super() cannot be used in a static method", expr)

        # Validate context: must have a current record
        if ctx.record_ctx.record is None:
            raise ctx.error("super() can only be used inside a class method", expr)

        # Validate: class must have a parent
        record_info = ctx.registry.get_record(ctx.record_ctx.record.name)
        if record_info is None or record_info.parent is None:
            raise ctx.error(
                f"super() requires a parent class, but '{ctx.record_ctx.record.name}' has no parent",
                expr
            )

        # Validate: no arguments (Python 3 style only)
        if expr.args:
            raise ctx.error("super() takes no arguments (Python 3 style)", expr)

        return SuperType(record_info.parent, ctx.record_ctx.record.name)

    def _try_resolve_method(self, expr: TpyMethodCall, obj_type: TpyType,
                            is_readonly_receiver: bool = False) -> TpyType | None:
        """Try to resolve method on obj_type. Returns return type or None."""
        result = self._analyze_instance_method(expr, obj_type, is_readonly_receiver)
        if result is not None:
            return result
        result = self._analyze_protocol_or_bound_method(expr, obj_type)
        if result is not None:
            return result
        return None

    def analyze_method_call(self, expr: TpyMethodCall) -> TpyType:
        """Analyze a method call."""
        # super().method() calls
        if isinstance(expr.obj, TpyCall) and expr.obj.func == "super":
            return self._analyze_super_method_call(expr)

        if isinstance(expr.obj, TpyName):
            # ClassName.staticmethod() pattern
            result = self._analyze_static_method_call(expr)
            if result is not None:
                return result

            # Reject method calls on enum types (enums have no class methods)
            if self.ctx.current_ns:
                binding = self.ctx.current_ns.lookup(expr.obj.name)
                if binding and binding.kind == BindingKind.ENUM:
                    raise self.ctx.error(
                        f"Enum type '{expr.obj.name}' has no method '{expr.method}'",
                        expr,
                    )

            # module.function() pattern (import X -> X.func())
            result = self._analyze_module_method_call(expr)
            if result is not None:
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
                    return result

        obj_type = self.expr.analyze_expr(expr.obj)

        # Pending generic instance: accumulate constraints from method calls
        if isinstance(obj_type, PendingGenericInstanceType):
            return self._analyze_pending_generic_method_call(expr, obj_type)

        # Unwrap ReadonlyType, remembering the flag for enforcement
        is_readonly_receiver = isinstance(obj_type, ReadonlyType)
        if isinstance(obj_type, ReadonlyType):
            obj_type = obj_type.wrapped

        if isinstance(obj_type, OwnType):
            obj_type = obj_type.wrapped
        elif isinstance(obj_type, OptionalType):
            if obj_type.inner.is_value_type():
                raise self.ctx.error(f"Cannot call method '{expr.method}' on type {obj_type}", expr)
            self.ctx.warning(OPTIONAL_NONE_ACCESS_WARNING, expr)
            expr.needs_optional_runtime_check = True
            obj_type = obj_type.inner

        # List mutation tracking (before deref chain -- applies to direct list types only)
        if isinstance(obj_type, (PendingListType, ListType)):
            if expr.method in LIST_MUTATION_METHODS:
                self.deduction.mark_list_mutated(expr.obj)

            # Infer/widen element type for empty list literals from mutation method args.
            # Pre-analyze the value arg to determine its type for inference,
            # then store all pre-analyzed arg types so _check_and_coerce_args
            # can reuse them (avoiding double-analysis).
            if isinstance(obj_type, PendingListType) and expr.args:
                pre_analyzed: list[TpyType] | None = None
                if expr.method == "append" and len(expr.args) == 1:
                    arg_type = self.expr.analyze_expr(expr.args[0])
                    self.deduction.infer_empty_list_element_type(expr.obj, arg_type)
                    pre_analyzed = [arg_type]
                elif expr.method == "insert" and len(expr.args) == 2:
                    idx_type = self.expr.analyze_expr(expr.args[0])
                    val_type = self.expr.analyze_expr(expr.args[1])
                    self.deduction.infer_empty_list_element_type(expr.obj, val_type)
                    pre_analyzed = [idx_type, val_type]
                # Update obj_type if element type changed (initial inference or widening)
                if pre_analyzed is not None:
                    info = self.ctx.list_literals.get(obj_type.literal_id)
                    if info and not isinstance(info.element_type, UnknownElementType):
                        if info.element_type != obj_type.element_type:
                            obj_type = PendingListType(info.element_type, obj_type.size, obj_type.literal_id)
                            self.ctx.set_expr_type(expr.obj, obj_type)
                            if isinstance(expr.obj, TpyName):
                                if self.ctx.current_scope:
                                    self.ctx.current_scope.define(expr.obj.name, obj_type)
                                if self.ctx.current_ns:
                                    self.ctx.current_ns.bind_variable(expr.obj.name, obj_type)
                    self.ctx.pre_analyzed_method_args[id(expr)] = pre_analyzed

        # Set element type inference from mutation methods on PendingSetType.
        if isinstance(obj_type, PendingSetType) and expr.method in ("add", "discard", "remove") and len(expr.args) == 1:
            arg_type = self.expr.analyze_expr(expr.args[0])
            self.deduction.infer_set_element_type(expr.obj, arg_type)
            info = self.ctx.set_literals.get(obj_type.literal_id)
            if info and not isinstance(info.element_type, UnknownElementType):
                if info.element_type != obj_type.element_type:
                    obj_type = PendingSetType(info.element_type, obj_type.literal_id)
                    self.ctx.set_expr_type(expr.obj, obj_type)
                    if isinstance(expr.obj, TpyName):
                        if self.ctx.current_scope:
                            self.ctx.current_scope.define(expr.obj.name, obj_type)
                        if self.ctx.current_ns:
                            self.ctx.current_ns.bind_variable(expr.obj.name, obj_type)
            self.ctx.pre_analyzed_method_args[id(expr)] = [arg_type]

        # Borrow conflict: structural mutation on a container with element-level borrows.
        # Resolves aliases so that alias.append() warns when items has element borrows.
        # Also handles field-path receivers (self.items.append()) via _storage_key.
        if isinstance(expr.obj, TpyName):
            storage = self.ctx.borrow_tracker.effective_storage(expr.obj.name)
        else:
            storage = _storage_key(expr.obj)
        if storage is not None:
            if self.ctx.borrow_tracker.has_element_borrow(storage):
                is_mutation = False
                if isinstance(obj_type, (PendingListType, ListType)):
                    is_mutation = expr.method in LIST_ITER_INVALIDATING
                elif isinstance(obj_type, (DictType, PendingDictType)):
                    is_mutation = expr.method in DICT_MUTATION_METHODS
                elif isinstance(obj_type, (SetType, PendingSetType)):
                    is_mutation = expr.method in SET_MUTATION_METHODS
                if is_mutation:
                    if self.ctx.borrow_tracker.has_iter_borrow(storage):
                        msg = (f"Mutation of '{storage}' while iterating over it"
                               f" ('{expr.method}' invalidates the iterator)")
                    else:
                        msg = (f"Mutation of '{storage}' while borrowed"
                               f" ('{expr.method}' may invalidate references)")
                    self.ctx.warning(msg, expr)

        # Deref chain -- resolves through Ptr (mutable and readonly) and any Deref[T] type
        original_type = obj_type
        current_type = obj_type
        deref_depth = 0
        while deref_depth <= 8:
            result = self._try_resolve_method(expr, current_type, is_readonly_receiver)
            if result is not None:
                expr.deref_depth = deref_depth
                if deref_depth > 0 and isinstance(original_type, PtrType):
                    obj_key = _expr_to_narrowing_key(expr.obj)
                    if obj_key is not None:
                        if obj_key in self.ctx.non_null_ptr_vars:
                            expr.ptr_non_null = True
                        if expr.loc:
                            self.ctx.ptr_deref_facts[
                                (expr.loc.line, obj_key)
                            ] = expr.ptr_non_null
                # Enforce readonly: cannot call non-readonly method on readonly receiver
                if is_readonly_receiver:
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
                if info is not None and not info.is_readonly:
                    from .statements import _root_name_of_expr
                    from ..parse.nodes import TpyName as _TpyName, TpyMethodCall as _TpyMethodCall
                    obj_root = _root_name_of_expr(expr.obj)
                    if obj_root is not None:
                        self.ctx.mark_loop_var_mutated(obj_root)
                        # For direct self.method() calls (expr.obj is exactly
                        # TpyName("self")), use call edges with receiver_is_self=True
                        # so Phase 2 can resolve transitively. For indirect cases
                        # like self.field.method(), mark self-mutation directly --
                        # mutating a field IS self-mutation and there is no callee
                        # self-mutation to propagate.
                        is_direct_self_call = (
                            isinstance(expr.obj, _TpyName) and expr.obj.name == "self"
                        )
                        if not is_direct_self_call:
                            self.ctx.mark_param_mutated(obj_root)
                            # Structural mutation: only invalidating methods that can
                            # reallocate storage (append/insert/clear/etc.), not
                            # element reads or field writes.
                            is_struct_mutation = False
                            if isinstance(obj_type, (PendingListType, ListType)):
                                is_struct_mutation = expr.method in LIST_ITER_INVALIDATING
                            elif isinstance(obj_type, (DictType, PendingDictType)):
                                is_struct_mutation = expr.method in DICT_MUTATION_METHODS
                            elif isinstance(obj_type, (SetType, PendingSetType)):
                                is_struct_mutation = expr.method in SET_MUTATION_METHODS
                            if is_struct_mutation:
                                self.ctx.mark_param_structurally_mutated(obj_root)
                        storage = self.ctx.borrow_tracker.effective_storage(obj_root)
                        self.ctx.mark_str_borrowers_mutated(storage)
                    else:
                        # Check for chained method calls rooted at self:
                        # self.get_span().sort() -- sort() is non-readonly and
                        # the span is a mutable view of self's storage.
                        # Treat as self-mutation conservatively.
                        chain = expr.obj
                        while isinstance(chain, _TpyMethodCall):
                            chain = chain.obj
                        chain_root = _root_name_of_expr(chain)
                        if chain_root == "self":
                            self.ctx.mark_param_mutated("self")
                return result

            deref_target = self.expr.get_deref_target_type(current_type)
            if deref_target is None:
                break
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
            obj_type = self.ctx.current_scope.lookup(name)
            if obj_type is not None and (isinstance(obj_type, PtrType)
                                         or isinstance(unwrap_readonly(obj_type), PtrType)):
                raise self.ctx.error(
                    f"Cannot call consuming method '{expr.method}' on pointer '{name}'; "
                    f"pointers do not own the pointee",
                    expr,
                )
            # Reject consuming outer variables inside a loop -- the variable
            # won't be re-bound on the next iteration, causing use-after-move.
            if self.ctx.loop_depth > 0:
                var_depth = self.ctx.var_scope_depth.get(name, 0)
                loop_scope_depth = self.ctx.current_scope.depth
                if var_depth < loop_scope_depth:
                    raise self.ctx.error(
                        f"Cannot consume '{name}' inside a loop; "
                        f"the variable is not re-bound each iteration",
                        expr,
                    )
            # Use-after-consume is already caught by _analyze_name before we get here.
            self.ctx.consumed_vars.add(name)

    def _analyze_static_method_call(self, expr: TpyMethodCall) -> TpyType | None:
        """Check for ClassName.staticmethod() pattern. Returns type or None if not a static call."""
        assert isinstance(expr.obj, TpyName)
        if self.ctx.current_ns is None:
            return None

        record_info = None
        binding = self.ctx.current_ns.lookup(expr.obj.name)
        if binding and binding.kind == BindingKind.RECORD:
            record_info = self.ctx.registry.get_record(expr.obj.name)
        elif binding and binding.kind == BindingKind.IMPORTED_NAME:
            # Builtin types imported from modules (e.g. from tpy import UInt8)
            import_info = self.ctx.imported_names.get(expr.obj.name)
            if import_info:
                qname = f"{import_info[0]}.{import_info[1]}"
                record_info = self.ctx.registry.get_builtin_record(qname)

        if record_info is None:
            return None

        overloads = record_info.get_method_overloads(expr.method)
        if not overloads:
            return None
        if not overloads[0].is_staticmethod:
            raise self.ctx.error(f"Method '{expr.method}' requires an instance (not a static method)", expr)

        if record_info.is_generic():
            return self._analyze_generic_static_method_call(expr, record_info, overloads)

        method = overloads[0]
        if method.type_params:
            # Non-generic class with method-level type params: type_args are for the method
            return self._analyze_generic_static_method_call(expr, record_info, overloads)

        if expr.type_args or expr.type_args_parse_error:
            raise self.ctx.error(
                f"'{record_info.name}' is not generic and does not accept type arguments",
                expr,
            )

        return_type = self._resolve_and_check_args(expr, overloads, {})
        expr.is_static_call = True
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
                        f"'{record_info.name}.{method.name}' expects {n_class} class type arguments "
                        f"or {n_total} total (class + method), got {n_given}",
                        expr)
                raise self.ctx.error(
                    f"'{record_info.name}' expects {n_class} type arguments, got {n_given}",
                    expr)

        virtual_func = FunctionInfo(
            name=method.name, params=method.params, return_type=method.return_type,
            is_staticmethod=method.is_staticmethod,
            type_params=all_type_params,
            type_param_bounds=all_bounds,
        )
        temp_call = TpyCall(func=expr.method, args=expr.args,
                            kwargs=expr.kwargs,
                            type_args=expr.type_args,
                            type_args_parse_error=expr.type_args_parse_error,
                            loc=expr.loc)
        result = self.calls._analyze_user_function_call(temp_call, [virtual_func])
        expr.args = temp_call.args
        expr.kwargs = temp_call.kwargs
        expr.resolved_function_info = temp_call.resolved_function_info
        expr.inferred_type_args = temp_call.inferred_type_args
        expr.is_static_call = True
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
            # Route through builtin path if the function is from a builtin module
            # or has a cpp_template (inline expansion, no C++ function body).
            is_builtin_func = (module_info.is_builtin
                               or overloads[0].is_builtin_function
                               or overloads[0].cpp_template is not None)
            if is_builtin_func:
                expr.builtin_module_call = module_name
                temp_call = TpyCall(func=expr.method, args=expr.args,
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
                return result
            else:
                expr.user_module_call = module_name
                temp_call = TpyCall(func=expr.method, args=expr.args,
                                    kwargs=expr.kwargs,
                                    type_args=expr.type_args,
                                    type_args_parse_error=expr.type_args_parse_error,
                                    loc=expr.loc)
                result = self.calls._analyze_user_function_call(temp_call, overloads)
                expr.args = temp_call.args
                expr.kwargs = temp_call.kwargs
                expr.resolved_function_info = temp_call.resolved_function_info
                expr.inferred_type_args = temp_call.inferred_type_args
                return result

        qname = f"{module_name}.{expr.method}"
        if record_info := self.ctx.registry.get_builtin_record(qname):
            if record_info.constructors and not record_info.type_params:
                expr.builtin_module_call = module_name
                temp_call = TpyCall(func=expr.method, args=expr.args, kwargs=expr.kwargs, loc=expr.loc)
                result = self.calls._check_builtin_constructor(temp_call, record_info)
                expr.args = temp_call.args
                expr.kwargs = temp_call.kwargs
                expr.resolved_function_info = temp_call.resolved_function_info
                return result

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
        if self.ctx.current_ns:
            binding = self.ctx.current_ns.lookup(current.name)
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
        if self.ctx.current_ns is None:
            return None
        binding = self.ctx.current_ns.lookup(name)
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
        info = self.ctx.pending_generic_instances.get(obj_type.instance_id)
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

        # Resolve kwargs
        if expr.kwargs:
            expr.args = resolve_kwargs(
                expr.args, expr.kwargs, method.params, expr.method,
                lambda msg: self.ctx.error(msg, expr),
                call_loc=expr.loc,
            )
            expr.kwargs = {}

        # Check arity
        if len(expr.args) < method.min_args or len(expr.args) > method.max_args:
            raise self.ctx.error(
                arity_error_msg(expr.method, method.min_args, method.max_args, len(expr.args)),
                expr)

        # Analyze arguments and accumulate constraints
        arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
        for (pname, ptype), arg_type in zip(method.params, arg_types):
            if not _contains_type_param_ref_type(ptype, set(info.type_params)):
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
            if isinstance(expr.obj, TpyName) and self.ctx.current_scope:
                self.ctx.current_scope.define(expr.obj.name, resolved_type)
            result = self._try_resolve_method(expr, resolved_type)
            if result is None:
                raise self.ctx.error(
                    f"'{resolved_type}' has no method '{expr.method}'", expr)
            return result

        # Not fully resolved yet -- check return type
        return_type = method.return_type
        if _contains_type_param_ref_type(return_type, set(info.type_params)):
            # Check if we can substitute what we have so far
            unresolved_in_return = _unresolved_params_in_type(return_type, info.inferred, set(info.type_params))
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
        )
        return return_type

    def try_resolve_pending_from_expected_type(
        self, pending: PendingGenericInstanceType, expected: TpyType,
        loc: 'SourceLocation | None' = None,
    ) -> NamedType | None:
        """Try to resolve a pending generic instance from an expected type.

        Used when a pending-type variable is passed to a typed parameter or
        returned where the function return type is known. Returns the resolved
        concrete type, or None if the expected type doesn't match.
        """
        info = self.ctx.pending_generic_instances.get(pending.instance_id)
        if info is None:
            return None

        # Unwrap Own/Optional/Readonly to find the inner NamedType
        target = expected
        if isinstance(target, OwnType):
            target = target.wrapped
        if isinstance(target, OptionalType):
            target = target.inner
        target = unwrap_readonly(target)

        if not isinstance(target, NamedType) or target.name != info.record_name:
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
        pattern = NamedType(info.record_name, tuple(pattern_args))

        # Match to extract constraints
        if not self.type_ops.match_type_with_inference(pattern, target, info.inferred):
            # Check if a previously-inferred param conflicts with the expected type
            for tp, expected_arg in zip(info.type_params, target.type_args):
                if tp in info.inferred and isinstance(expected_arg, TpyType):
                    if info.inferred[tp] != expected_arg:
                        from .diagnostics import SemanticError
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

    def _eagerly_resolve_pending_generic(self, info: 'PendingGenericInstanceInfo') -> NamedType:
        """Resolve a pending generic instance to a concrete NamedType."""
        type_args = tuple(info.inferred[tp] for tp in info.type_params)
        resolved_type = NamedType(info.record_name, type_args)

        # Validate type param bounds
        for param_name, type_arg in zip(info.type_params, type_args):
            if param_name in info.record_info.type_param_bounds:
                bound = info.record_info.type_param_bounds[param_name]
                if not self.protocols.type_conforms_to_protocol(type_arg, bound):
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
            if self.ctx.current_scope:
                self.ctx.current_scope.define(info.variable_name, resolved_type)
            if self.ctx.current_ns:
                self.ctx.current_ns.bind_variable(info.variable_name, resolved_type)
            var_decl = self.ctx.var_decl_by_name.get(info.variable_name)
            if var_decl:
                self.ctx.var_types[id(var_decl)] = resolved_type
            self.ctx.declared_var_types[(info.decl_line, info.variable_name)] = resolved_type

        # Set constructor info now that we have concrete types
        type_subst = info.inferred
        self.calls._set_record_constructor_info(info.expr, info.record_info, resolved_type, type_subst)

        # Clean up tracking
        del self.ctx.pending_generic_instances[info.instance_id]
        self.ctx.variable_to_generic_instance.pop(info.variable_name, None)

        return resolved_type

    def _analyze_instance_method(self, expr: TpyMethodCall, obj_type: TpyType,
                                  is_readonly_receiver: bool = False) -> TpyType | None:
        """Analyze instance method call on any type (builtin or user record)."""
        record_info = self.ctx.registry.get_record_for_type(obj_type)
        if not record_info:
            return None
        overloads, inherited_subst = self.protocols.lookup_record_method_overloads(
            record_info, expr.method)
        if not overloads:
            return None
        instance_subst = self.type_ops.build_type_substitution(obj_type)
        if inherited_subst and instance_subst:
            type_subst = {
                k: self.type_ops.substitute_type_params(v, instance_subst)
                for k, v in inherited_subst.items()
            }
        elif inherited_subst:
            type_subst = inherited_subst
        else:
            type_subst = instance_subst

        # Tie-breaking for auto_readonly clones (mutable + const overload pair):
        # readonly receiver prefers the readonly overload, mutable receiver prefers mutable.
        if is_readonly_receiver:
            ro = [m for m in overloads if m.is_readonly]
            if ro:
                overloads = ro
        else:
            mut = [m for m in overloads if not m.is_readonly]
            if mut:
                overloads = mut

        # Check if the method has its own type parameters (generic method).
        # Generic methods don't support multiple overloads; user-defined methods
        # always register a single overload per name (registration.py).
        method_info = overloads[0]
        if method_info.is_generic():
            if len(overloads) > 1:
                raise self.ctx.error(
                    f"Overloaded generic methods are not supported for '{expr.method}'", expr)
            return self._analyze_generic_method_call(
                expr, method_info, record_info, type_subst)

        return self._resolve_and_check_args(expr, overloads, type_subst)

    def _analyze_generic_method_call(
        self, expr: TpyMethodCall, method_info: FunctionInfo,
        record_info, class_subst: dict[str, TpyType | int],
    ) -> TpyType:
        """Analyze a call to a generic method (method with its own type parameters).

        Handles two kinds of method type params:
        - New params: type params not in the class (e.g. U on def transform[U])
        - Constrained class params: class type params with an additional method-level bound
        """
        class_type_params = set(record_info.type_params) if record_info.type_params else set()
        new_params = [tp for tp in method_info.type_params if tp not in class_type_params]
        constrained_class_params = [tp for tp in method_info.type_params if tp in class_type_params]

        # Validate per-method bounds on class type params: check that the concrete
        # class type satisfies the method's bound. These are class-level type params,
        # so they must be in class_subst for any fully-instantiated generic class.
        for tp in constrained_class_params:
            if tp not in class_subst:
                raise self.ctx.error(
                    f"Method '{method_info.name}' has bound on class type parameter '{tp}', "
                    f"but the class is not instantiated with a concrete type for '{tp}'",
                    expr)
            if tp in method_info.type_param_bounds:
                bound = method_info.type_param_bounds[tp]
                concrete_type = class_subst[tp]
                if isinstance(concrete_type, TpyType) and not self.protocols.type_conforms_to_protocol(
                        concrete_type, bound):
                    raise self.ctx.error(
                        f"Method '{method_info.name}' requires type parameter '{tp}' to satisfy "
                        f"'{bound}', but '{concrete_type}' does not conform",
                        expr)

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
        )

        # Pre-substitute class params in the method signature so inference
        # only needs to resolve new params
        if class_subst:
            partial_func = self.type_ops.substitute_method_type_params(partial_func, class_subst)

        # Infer new params from arguments or explicit type args
        has_wildcards = expr.type_args and None in expr.type_args
        if expr.type_args:
            if len(expr.type_args) != len(new_params):
                raise self.ctx.error(
                    f"Method '{method_info.name}' expects {len(new_params)} type argument(s), "
                    f"got {len(expr.type_args)}",
                    expr)
            if not has_wildcards:
                method_subst = dict(zip(new_params, expr.type_args))
            else:
                arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
                method_subst = self.type_ops.infer_type_params_for_function(
                    partial_func, arg_types, self.protocols.type_conforms_to_protocol,
                    expected_return_type=self.ctx.expr_type_hint,
                    explicit_type_args=expr.type_args,
                )
                if method_subst is None:
                    raise self.ctx.error(
                        f"Cannot infer type arguments for method '{method_info.name}'. "
                        f"Specify explicitly: .{method_info.name}[{', '.join(new_params)}](...)",
                        expr)
        else:
            arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
            method_subst = self.type_ops.infer_type_params_for_function(
                partial_func, arg_types, self.protocols.type_conforms_to_protocol,
                expected_return_type=self.ctx.expr_type_hint,
            )
            if method_subst is None:
                raise self.ctx.error(
                    f"Cannot infer type arguments for method '{method_info.name}'. "
                    f"Specify explicitly: .{method_info.name}[{', '.join(new_params)}](...)",
                    expr)

        # Validate bounds for new params (inference checks bounds internally,
        # but explicit type args bypass inference)
        new_param_bounds = {k: v for k, v in method_info.type_param_bounds.items()
                           if k in set(new_params)}
        if new_param_bounds:
            validate_type_param_bounds(
                method_subst, new_param_bounds, method_info.name,
                self.protocols.type_conforms_to_protocol,
                lambda msg: self.ctx.error(msg, expr),
            )

        # Prefer StrView for string literal args (skip fully-explicit)
        if not expr.type_args or has_wildcards:
            prefer_strview_for_literals(method_subst, partial_func, expr.args,
                                       self.protocols.type_conforms_to_protocol)

        # Store inferred type args (new params only) for codegen
        expr.inferred_type_args = tuple(method_subst[p] for p in new_params)

        # Merge class subst + method subst for full substitution
        full_subst = dict(class_subst) if class_subst else {}
        full_subst.update(method_subst)

        return self._resolve_and_check_args(expr, [method_info], full_subst)

    def _is_protocol_method_readonly(self, protocol_name: str, method_name: str) -> bool:
        """Check if a protocol method is readonly (per-method or protocol-level).

        Searches inherited methods too, so a @readonly method from a parent
        protocol is correctly recognized.
        """
        proto_info = self.ctx.registry.get_protocol(protocol_name)
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
                                     cpp_template: str | None = None) -> FunctionInfo:
        """Build a FunctionInfo for a protocol method signature."""
        params = [ParamInfo(n, t) for n, t in raw_params]
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
            raw_params, return_type, cpp_template = method_sig
            fi = self._build_protocol_method_info(obj_type.name, expr.method, raw_params, return_type, cpp_template)
            return self._resolve_and_check_args(expr, [fi], {})

        if isinstance(obj_type, TypeParamRef):
            bound = self.type_ops.get_type_param_bound(obj_type.name)
            if bound is not None and is_protocol_type(bound):
                method_sig = self.protocols.get_protocol_method_signature(bound, expr.method, self_type=obj_type)
                if method_sig is None:
                    raise self.ctx.error(f"Protocol '{bound.name}' has no method '{expr.method}'", expr)
                raw_params, return_type, cpp_template = method_sig
                fi = self._build_protocol_method_info(bound.name, expr.method, raw_params, return_type, cpp_template)
                return self._resolve_and_check_args(expr, [fi], {})

        return None

    def _analyze_super_method_call(self, expr: TpyMethodCall) -> TpyType:
        """Analyze a super().method() call.

        The method is looked up in the parent class and type arguments are
        substituted for generic parent classes.
        """
        # Analyze super() to get the SuperType
        assert isinstance(expr.obj, TpyCall) and expr.obj.func == "super"
        super_type = self._analyze_super_call_static(self.ctx, expr.obj)
        assert isinstance(super_type, SuperType)

        parent_type = super_type.parent_type
        parent_info = self.ctx.registry.get_record_for_type(parent_type)
        if parent_info is None:
            raise self.ctx.error(f"Parent class '{parent_type}' not found", expr)

        # Special handling for super().__init__()
        if expr.method == "__init__":
            # super().__init__() can only be called inside __init__
            if self.ctx.current_function is None or self.ctx.current_function.name != "__init__":
                raise self.ctx.error(
                    "super().__init__() can only be called inside __init__",
                    expr
                )
            # Check for duplicate super().__init__() calls
            if self.ctx.super_init_call is not None:
                raise self.ctx.error(
                    "super().__init__() can only be called once",
                    expr
                )
            # Track this call for later validation (must be first statement)
            self.ctx.super_init_call = expr

            # Check for __init__ method or constructors (builtin types use constructors)
            init_overloads = parent_info.get_method_overloads("__init__")
            if not init_overloads and parent_info.constructors:
                # Builtin type with constructors - use those as overloads
                init_overloads = parent_info.constructors

            if not init_overloads:
                # Parent has no explicit __init__ or constructors, allow with no arguments
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
            if self.ctx.current_function is None or self.ctx.current_function.name != "__del__":
                raise self.ctx.error(
                    "super().__del__() can only be called inside __del__",
                    expr
                )
            # Check for duplicate super().__del__() calls
            if self.ctx.super_del_call is not None:
                raise self.ctx.error(
                    "super().__del__() can only be called once",
                    expr
                )
            # Track this call for later validation (must be last statement)
            self.ctx.super_del_call = expr
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
            isinstance(self.ctx.current_function, TpyFunction)
            and self.ctx.current_function.is_readonly
        )

        # Look up the method in the parent class
        # For __init__, we already have init_overloads; for other methods, look up
        if expr.method == "__init__":
            overloads = init_overloads
        else:
            overloads = parent_info.get_method_overloads(expr.method)
        if not overloads:
            raise self.ctx.error(
                f"Parent class '{parent_type}' has no method '{expr.method}'",
                expr
            )

        # Build type substitution for generic parent (e.g., Container[Int32] -> {"T": Int32})
        type_subst = self.protocols._get_parent_type_subst(parent_type, parent_info)

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
        expr.super_parent_type = parent_type
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

        Docstrings are expression statements containing a string literal.
        Returns None if all statements are docstrings or list is empty.
        """
        for stmt in stmts:
            # Skip docstrings (expression statements with string literals)
            if isinstance(stmt, TpyExprStmt) and isinstance(stmt.expr, TpyStrLiteral):
                continue
            return stmt
        return None

    @staticmethod
    def find_last_non_docstring_stmt(stmts: list[TpyStmt]) -> TpyStmt | None:
        """Find the last statement, skipping only a leading docstring.

        Only the first statement can be a docstring (string-literal expression).
        Trailing string literals are regular statements, not docstrings.
        """
        if not stmts:
            return None
        # A single string-literal expression is a docstring-only body
        if len(stmts) == 1 and isinstance(stmts[0], TpyExprStmt) and isinstance(stmts[0].expr, TpyStrLiteral):
            return None
        return stmts[-1]

