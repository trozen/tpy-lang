"""
TurboPython Statement Code Generation

Generates C++ code from TurboPython statements.
"""

from __future__ import annotations
import io
from typing import Callable, TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, PtrType,
    PendingListType, PendingDictType, PendingSetType, PendingStrType, PendingViewType, OwnType, OptionalType,
    NoneType, NominalType, AnyType, STR, BYTES, TupleType, VoidType,
    INT32, BIGINT, FLOAT, is_protocol_type, ALL_FIXED_INTS,
    ReadonlyType, unwrap_readonly, unwrap_optional_own, TypeParamRef, UnionType, LiteralType, LiteralTag,
    is_own_pointer_repr_optional,
    resolve_int_literals,
    error_return_to_cpp, qualify_exception_name, is_return_exception,
    unwrap_ref_type, RefType, unwrap_qualifiers,
    is_void_like_type,
)
from ..parse import (
    TpyStmt, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign, TpyDelItem, TpyDelVar, TpyDelAttr, TpyExprStmt, TpyReturn, TpyYield,
    TpyIf, TpyWhile, TpyForEach, TpyBreak, TpyContinue, TpyPassStmt,
    TpyRaise, TpyExceptHandler, TpyTry, TpyWith,
    TpyGlobal, TpyNonlocal, TpyNestedDef,
    TpyImport, TpySubscript, TpySlice, TpyStrLiteral, TpyNoneLiteral, TpyName, TpyExpr, TpyFunction,
    TpyAssert, TpyBoolLiteral, TpyArrayLiteral,
    TpyFieldAccess, TpyMethodCall,
    TpyBinOp, TpyCall, TpyIntLiteral, TpyUnaryOp, TpyCoerce, TpyIfExpr,
    TpyMatch,
)
from ..namespace import Namespace
from ..symbol_binding import SymbolKind
from ..sema.context import PENDING_CONTAINER_TYPES
from ..sema.literal_utils import literal_value_from_expr
from ..typesys import view_family_for_type
from ..diagnostics import SemanticError
from ..liveness import stmts_terminate

from .context import INDENT, CodeGenError, FinallyContext, LocalCppForm, escape_cpp_name, qualified_cpp_name, loop_var_binding, is_lvalue_iterable, view_key_target
from ..type_def_registry import (
    is_list,
    is_fixed_int_type, is_big_int_type, is_bytes_type, is_str_type,
    is_str_view_type, is_bytes_view_type, is_string_type,
    protocol_info_of,
)
from .expressions import _is_concrete_user_record
from .functions import default_to_cpp
from .type_resolution import resolve_stmt_binding_type
from ..prescan import match_is_none
from .match import MatchGenerator
from .gen_async import POLL_VOID_READY_RETURN

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver
    from .expressions import ExpressionGenerator
    from .builtins import BuiltinGenerator
    from .protocols import ProtocolGenerator


class StatementGenerator:
    """Generates C++ code from TurboPython statements."""

    def __init__(
        self,
        ctx: CodeGenContext,
        types: TypeResolver,
        builtins: BuiltinGenerator,
        protocols: ProtocolGenerator,
        expressions: ExpressionGenerator,
    ):
        self.ctx = ctx
        self.types = types
        self.builtins = builtins
        self.protocols = protocols
        self.expressions = expressions
        self.match = MatchGenerator(ctx, types, expressions, self)
        self._reassigned_param_copies: list[tuple[str, TpyType]] = []

    def _gen_buffered_body(self, out: TextIO, stmts: list[TpyStmt],
                           track_stmt_line: bool = False) -> None:
        """Buffer body statements, prepend hoist declarations, write to output."""
        body_buf = io.StringIO()
        # Emit mutable local copies for reassigned const-ref params
        if self._reassigned_param_copies:
            indent = self.ctx.indent()
            for pname, ptype in self._reassigned_param_copies:
                cpp_name = escape_cpp_name(pname)
                cpp_type = ptype.to_cpp()
                param_ref = f"__param_{cpp_name}"
                if isinstance(ptype, OptionalType) and is_str_type(ptype.inner):
                    init = (f"{param_ref} ? std::make_optional("
                            f"std::string(*{param_ref})) : std::nullopt")
                else:
                    init = param_ref
                body_buf.write(f"{indent}{cpp_type} {cpp_name} = {init};\n")
            self._reassigned_param_copies = []
        for stmt in stmts:
            if self.ctx.overload_terminated:
                break
            if track_stmt_line:
                self.ctx.current_stmt_line = stmt.loc.line if hasattr(stmt, 'loc') and stmt.loc else 0
            self.gen_stmt(body_buf, stmt)
        if track_stmt_line:
            self.ctx.current_stmt_line = 0
        hoist_indent = INDENT * self.ctx.indent_level
        for decl in self.ctx.pending_hoist_decls:
            out.write(f"{hoist_indent}{decl}")
        out.write(body_buf.getvalue())

    def gen_body(self, out: TextIO, body: list[TpyStmt],
                 params: list[tuple[str, TpyType]], return_type: TpyType,
                 func: TpyFunction, local_ns: Namespace,
                 indent_level: int = 1, is_method: bool = False,
                 record_type_param_bounds: dict[str, TpyType] | None = None,
                 const_ref_params: set[str] | None = None,
                 deep_const_borrow_params: set[str] | None = None) -> None:
        """Generate the body of a function or method.

        Handles scope setup, body buffering, hoist-decl prepending, and cleanup.
        Shared by gen_function_def() and _gen_method().
        """
        self.ctx.reset_scope()
        # Apply literal overload facts (injected by _gen_literal_specialized_function,
        # survives reset_scope like overload_param_types)
        if self.ctx.literal_overload_facts:
            self.ctx.literal_facts.update(self.ctx.literal_overload_facts)
        self.ctx.const_ref_params = const_ref_params if const_ref_params is not None else set()
        self.ctx.deep_const_borrow_params = deep_const_borrow_params if deep_const_borrow_params is not None else set()
        self.ctx.declared_vars = {pname for pname, _ in params}
        self.ctx.var_types = {pname: unwrap_ref_type(ptype) for pname, ptype in params}
        self.ctx.local_scope_names = {pname for pname, _ in params}
        self.ctx.global_declared_vars = self.ctx.analyzer.function_global_decls.get(id(func), set())
        scan = self.ctx.analyzer.function_scan_results.get(id(func))
        if scan:
            self.ctx.reassigned_vars = scan.reassigned - self.ctx.global_declared_vars
            self.ctx.rvalue_reassigned_vars = scan.rvalue_reassigned - self.ctx.global_declared_vars
            self.ctx.lvalue_reassigned_vars = scan.lvalue_reassigned - self.ctx.global_declared_vars
            self.ctx.aliased_vars = set(scan.alias_sources.values())
            self.ctx.alias_names = scan.initial_alias_names
        else:
            self.ctx.reassigned_vars = set()
            self.ctx.rvalue_reassigned_vars = set()
            self.ctx.lvalue_reassigned_vars = set()
            self.ctx.aliased_vars = set()
            self.ctx.alias_names = set()
        self.ctx.hoisted_vars = self.ctx.analyzer.function_hoisted_vars.get(id(func), set())
        self.ctx.move_through_vars = self.ctx.analyzer.function_move_through_vars.get(id(func), set())
        self.ctx.sema_movable_locals = self.ctx.analyzer.function_movable_locals.get(id(func), set())
        # Optional non-value params are T* / const T* in C++ -- need pointer-local treatment (->)
        for pname, ptype in params:
            actual = unwrap_readonly(ptype)
            if self.protocols.is_static_protocol_param(ptype):
                # Static protocol params: check if nullable (uses pointer repr)
                infos = self.protocols.get_all_protocol_params([(pname, ptype)])
                if infos and infos[0].has_none:
                    self.ctx.pointer_locals.add(pname)
                    self.ctx.const_indirect_locals.add(pname)
            elif isinstance(actual, OptionalType) and actual.uses_pointer_repr():
                self.ctx.pointer_locals.add(pname)
                if isinstance(ptype, ReadonlyType):
                    self.ctx.const_indirect_locals.add(pname)
            # Own[OptionalType[P_ref]]: param renders as `std::optional<P>&&`
            # (storage form), but body access patterns are the same as a
            # storage-form Optional local: arrow for member access (uses
            # optional<P>::operator->), .has_value() for null check, direct
            # std::move into another storage slot. Register as both
            # pointer_local (for arrow access) and optional_local (so the
            # null-check dispatch picks has_value over `!= nullptr`). Rebind
            # the namespace to the bare Optional so type-aware codegen sites
            # match the sibling pointer-repr Optional handling. movable_locals
            # is set below via the generic `unwrap_optional_own + non-value`
            # pass.
            elif is_own_pointer_repr_optional(actual):
                self.ctx.pointer_locals.add(pname)
                self.ctx.optional_locals.add(pname)
                self.ctx.var_types[pname] = actual.wrapped
                local_ns.bind_variable(pname, actual.wrapped)
            # Non-value union params are pointer variants (variant<T*...>)
            elif self.ctx.is_ptr_variant_union(actual):
                self.ctx.ptr_variant_locals.add(pname)
                if isinstance(ptype, ReadonlyType):
                    self.ctx.const_indirect_locals.add(pname)
            # Own[T] and Own[T] | None params are movable (caller gave up ownership)
            own_actual = unwrap_optional_own(actual)
            if own_actual is not None and not own_actual.wrapped.is_value_type():
                self.ctx.movable_locals.add(pname)
            # Own[tuple[T | None, ...]] params are stored in storage form
            # (std::tuple<std::optional<T>, ...>); same C++ shape as the
            # storage-form locals registered for storage-form tuple iteration.
            if isinstance(actual, OwnType):
                inner = unwrap_readonly(actual.wrapped)
                if isinstance(inner, TupleType) and inner.has_pointer_repr_optional_element():
                    self.ctx.storage_form_tuple_locals.add(pname)
            # Value-optional params (std::optional<T> by value) are movable when
            # the inner type has an expensive copy (String, BigInt, etc.).
            # readonly params are excluded to respect the no-mutation contract.
            elif (isinstance(actual, OptionalType) and not actual.uses_pointer_repr()
                    and not isinstance(ptype, ReadonlyType)
                    and actual.inner.is_expensive_copy()):
                self.ctx.movable_locals.add(pname)
        # Generator-promoted locals are struct fields; pre-seed var_types
        # so codegen sites that consult it (e.g. address-of for tuple
        # slots) see the original TPy type rather than the synthetic
        # outer-optional wrapper used for init tracking.
        if func.generator_locals:
            for lname, ltype in func.generator_locals:
                self.ctx.var_types[lname] = ltype
        self.ctx.current_ns = local_ns
        self.ctx.indent_level = indent_level
        self.ctx.current_return_type = return_type
        # Set current_yield_type for generator bodies so yield-emission sites
        # don't need it threaded through their call signatures. Skipped for
        # sema-errored generators (no resolved yield type) -- leaves the
        # field at its reset_scope() default rather than crashing later.
        if func.is_generator and func.generator_yield_type is not None:
            self.ctx.current_yield_type = func.generator_yield_type
        raw_error_return = getattr(func, 'error_return', None)
        self.ctx.current_error_return = error_return_to_cpp(raw_error_return, self.ctx.analyzer.ctx.module_name, self.ctx.analyzer.registry) if raw_error_return else None
        self.ctx.current_func_params = {pname: ptype for pname, ptype in params}
        self.ctx.in_property_getter = getattr(func, 'is_property_getter', False)
        self.ctx.current_type_param_bounds = dict(record_type_param_bounds) if record_type_param_bounds else {}
        if func.type_param_bounds:
            self.ctx.current_type_param_bounds.update(func.type_param_bounds)
        if is_method:
            self.ctx.in_method = True

        # Emit mutable local copies for reassigned const-ref params (BigInt, str)
        # so internal reassignment doesn't change the function signature.
        self._reassigned_param_copies = []
        if scan:
            for pname, ptype in params:
                if pname in scan.reassigned and ptype.param_needs_copy_for_reassign():
                    self._reassigned_param_copies.append((pname, ptype))

        # @overload short-arity stub: emit the impl params that the stub
        # omitted as locals initialized to the impl's defaults.
        missing_locals = self.ctx.overload_missing_param_locals
        if missing_locals:
            from ..typesys import NoneType as _NoneType
            self.ctx.overload_missing_param_locals = []
            reassigned = scan.reassigned if scan else set()
            indent = self.ctx.indent()
            for pname, ptype, default_expr in missing_locals:
                # NoneType narrowing: accessing an Optional[T] value always
                # requires a guard (if x is not None). When narrowed to None
                # the guard folds to False, dead-branch elim strips the
                # value-access path, so the local is guaranteed unused.
                # Literal narrowing does NOT get this treatment -- the body
                # may use the param directly without any conditional.
                narrowed = self.ctx.overload_param_types.get(pname)
                if isinstance(narrowed, _NoneType) and pname not in reassigned:
                    continue
                cpp_default = default_to_cpp(self.ctx, default_expr, ptype)
                # default_to_cpp falls back to "0" for unrecognized exprs.
                # Use C++ value-initialization ({}) instead -- valid for any
                # default-constructible type (empty vector, 0 for ints, etc.).
                if cpp_default == "0" and not isinstance(default_expr, (TpyIntLiteral, TpyCall)):
                    cpp_default = "{}"
                # Use the storage type (to_cpp), not the param-passing type
                # (to_cpp_param_type) which may be a const reference.
                cpp_type = ptype.to_cpp()
                cpp_name = escape_cpp_name(pname)
                out.write(f"{indent}{cpp_type} {cpp_name} = {cpp_default};\n")
                self.ctx.declared_vars.add(pname)
                self.ctx.local_scope_names.add(pname)
                self.ctx.var_types[pname] = unwrap_ref_type(ptype)
                local_ns.bind_variable(pname, ptype)
                # Mirror the pointer-local / ptr-variant registration that
                # gen_body does for real params of the same shapes, so the
                # body's access-path codegen (-> vs ., variant extraction,
                # move semantics) treats the missing-param local correctly.
                actual = unwrap_readonly(ptype)
                if isinstance(actual, OptionalType) and actual.uses_pointer_repr():
                    self.ctx.pointer_locals.add(pname)
                    if isinstance(ptype, ReadonlyType):
                        self.ctx.const_indirect_locals.add(pname)
                elif self.ctx.is_ptr_variant_union(actual):
                    self.ctx.ptr_variant_locals.add(pname)
                    if isinstance(ptype, ReadonlyType):
                        self.ctx.const_indirect_locals.add(pname)

        self._gen_buffered_body(out, body)

        # Void @error_return functions need explicit success return to avoid UB
        if self.ctx.current_error_return and isinstance(return_type, VoidType):
            out.write(f"{self.ctx.indent()}return {{}};\n")

        # Skip trailing-comment scan when overload dead-branch elim resolved
        # the body's top-level if to True -- the emitted stmts come from a
        # then_body, not the TpyIf node, so scanning forward from the TpyIf's
        # line would pick up comments from inside the dead branches.
        if not self.ctx.overload_terminated:
            self.ctx.emit_block_trailing_comments(out, body, self.ctx.indent())

        if is_method:
            self.ctx.in_method = False
        self.ctx.local_scope_names = set()
        self.ctx.indent_level = 0
        self.ctx.current_ns = None

    def gen_stmt(self, out: TextIO, stmt: TpyStmt) -> None:
        """Generate a statement."""
        indent = self.ctx.indent()

        # Track current line for order-aware import qualification in top-level context
        # (current_stmt_line > 0 means we're in top-level, set by gen_module_init)
        if self.ctx.current_stmt_line > 0 and hasattr(stmt, 'loc') and stmt.loc:
            self.ctx.current_stmt_line = stmt.loc.line

        self.ctx.emit_inline_comments(out, stmt.loc, indent)

        # Compound statements - delegate to handlers (they flush before their header)
        if isinstance(stmt, TpyIf):
            # Source comment is emitted inside _gen_if so that overload
            # dead-branch elim can suppress it when the `if` is resolved away.
            self._gen_if(out, stmt, indent)
        elif isinstance(stmt, TpyWhile):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._gen_while(out, stmt, indent)
        elif isinstance(stmt, TpyForEach):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._emit_branch_decls(out, stmt, indent)
            self._gen_for_each(out, stmt, indent)
        elif isinstance(stmt, TpyAssert):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._gen_assert(out, stmt, indent)
        elif isinstance(stmt, TpyTupleUnpack):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._gen_tuple_unpack(out, stmt, indent)
        elif isinstance(stmt, TpyMatch):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self.match.gen_match(out, stmt, indent)
        elif isinstance(stmt, TpyTry):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._emit_branch_decls(out, stmt, indent)
            self._gen_try(out, stmt, indent)
        elif isinstance(stmt, TpyWith):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._emit_branch_decls(out, stmt, indent)
            self._gen_with(out, stmt, indent)
        elif isinstance(stmt, TpyNestedDef):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self.ctx.temps.flush(out, indent)
            self._gen_nested_def(out, stmt, indent)
        elif isinstance(stmt, TpyYield):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self.ctx.temps.flush(out, indent)
            self._gen_yield(out, stmt, indent)
        else:
            # Simple statements - single flush point for all
            code = self._gen_simple_stmt(stmt, indent)
            if code is not None:
                self.ctx.emit_source_comment(out, stmt.loc, indent)
                self.ctx.temps.flush(out, indent)
                out.write(code)
            # Assignment narrowing for union VarDecl
            if isinstance(stmt, TpyVarDecl):
                # Clear stale narrowing on any write to this variable
                self.ctx.assign_narrowed_types.pop(stmt.name, None)
                self.ctx.literal_facts.pop(stmt.name, None)
                if stmt.then_type_facts:
                    for var_name, narrowed_type in stmt.then_type_facts.items():
                        self.ctx.assign_narrowed_types[var_name] = narrowed_type

    def _gen_simple_stmt(self, stmt: TpyStmt, indent: str) -> str | None:
        """Generate code for simple statements. Returns code to write or None.

        Expression generation happens here (which may create temps).
        The caller handles flushing temps before writing the returned code.
        """
        if isinstance(stmt, TpyVarDecl):
            if stmt.init and self._get_error_return_fi(stmt.init):
                if self.ctx.try_except_label:
                    return self._gen_error_return_var_decl(stmt, indent)
                if self.ctx.current_error_return:
                    return self._gen_error_return_propagate_var_decl(stmt, indent)
                # Top-level: unwrap with panic on error
                return self._gen_error_return_unwrap_var_decl(stmt, indent)
            return self._gen_var_decl_code(stmt, indent)
        elif isinstance(stmt, TpyAssign):
            if self._get_error_return_fi(stmt.value):
                if self.ctx.try_except_label:
                    return self._gen_error_return_assign(stmt, indent)
                if self.ctx.current_error_return:
                    return self._gen_error_return_propagate_assign(stmt, indent)
                return self._gen_error_return_unwrap_assign(stmt, indent)
            return self._gen_assign_code(stmt, indent)
        elif isinstance(stmt, TpyAugAssign):
            return self._gen_aug_assign_code(stmt, indent)
        elif isinstance(stmt, TpyDelItem):
            return self._gen_del_item_code(stmt, indent)
        elif isinstance(stmt, TpyDelVar):
            return self._gen_del_var_code(stmt, indent)
        elif isinstance(stmt, TpyDelAttr):
            return self._gen_del_attr_code(stmt, indent)
        elif isinstance(stmt, TpyExprStmt):
            if isinstance(stmt.expr, TpyStrLiteral):
                return None  # Skip docstrings
            if self._get_error_return_fi(stmt.expr):
                return self._gen_error_return_stmt_block(
                    self._gen_error_return_call(stmt.expr), indent)
            return f"{indent}{self.expressions.gen_expr(stmt.expr)};\n"
        elif isinstance(stmt, TpyReturn):
            if self.ctx.in_async_coro_body:
                return self._make_async_return(stmt, indent)
            if self.ctx.in_generator_body:
                # Bare return in generator -> StopIteration
                return f"{indent}goto __done;\n"
            if stmt.value:
                ret_type = self.ctx.current_return_type
                ret_value = stmt.value
                # In @overload specialization: validate return type and strip
                # wrong-target coercions. Sema coerced against the impl's union
                # return type, which may have picked the wrong union member.
                if (self.ctx.overload_param_types or self.ctx.literal_overload_facts) and stmt.value_type is not None:
                    compatible = self._check_overload_return_type(stmt, ret_type)
                    if not compatible:
                        if self.ctx.literal_overload_facts:
                            return None  # Dead code after literal branch elimination
                        from .context import CodeGenError
                        vt = stmt.value_type
                        if isinstance(vt, IntLiteralType):
                            vt = BIGINT
                        raise CodeGenError(
                            f"@overload return type mismatch: returning '{vt}' "
                            f"but this overload declares '-> {ret_type}'",
                            loc=stmt.loc,
                        )
                    ret_value = self._strip_wrong_overload_coerce(ret_value, ret_type)
                # Property getter with pointer-repr return: return field directly
                # (C++ return is std::optional<T>& / std::variant<A,B>&, not T* / variant<T*>)
                if (self.ctx.in_property_getter
                        and ((isinstance(ret_type, OptionalType) and ret_type.uses_pointer_repr())
                             or self.ctx.is_ptr_variant_union(ret_type))):
                    ret_expr = self.expressions.gen_expr(ret_value)
                    return self._make_return(indent, ret_expr)
                if isinstance(ret_type, OptionalType):
                    if not ret_type.uses_pointer_repr():
                        if isinstance(ret_value, TpyNoneLiteral):
                            return self._make_return(indent, "std::nullopt")
                        ret_expr = self.expressions.gen_expr_deref(ret_value, ret_type)
                        return self._make_return(indent, ret_expr)
                    ret_expr = self.expressions._optional_pointer_form_value(ret_value, ret_type)
                    return self._make_return(indent, ret_expr)
                # Recursive union wrapper struct: `return None` constructs the
                # monostate variant (NoneType is one of the wrapper's members).
                if isinstance(ret_value, TpyNoneLiteral):
                    if self.ctx.is_recursive_union(unwrap_qualifiers(ret_type)):
                        return self._make_return(indent, "std::monostate{}")
                # Pointer-variant union return: return variant<T*...>
                if self.ctx.is_ptr_variant_union(ret_type):
                    if isinstance(ret_value, TpyNoneLiteral):
                        return self._make_return(indent, "std::monostate{}")
                    # Check if source is a ptr-variant AND not currently narrowed.
                    # Narrowed ptr-variant vars resolve to Dog& (via std::get), so
                    # they need &() to produce Dog* for the return variant.
                    is_narrowed = (isinstance(ret_value, TpyName)
                                   and ret_value.name in self.ctx.narrowed_vars)
                    if self.ctx.is_ptr_variant_source(ret_value) and not is_narrowed:
                        ret_expr = self.expressions.gen_expr(ret_value, ret_type)
                        return self._make_return(indent, ret_expr)
                    # VALUE_VARIANT source (Own[A|B] param) returning into a
                    # pointer-variant return: lift via to_ptr_variant. The
                    # Own param's C++ shape is value-variant; without the lift
                    # codegen would take the address of the storage and produce
                    # variant<A,B>* rather than variant<A*,B*>.
                    if (isinstance(ret_value, TpyName) and not is_narrowed
                            and self.ctx.needs_to_ptr_variant_lift(ret_value.name)):
                        ret_expr = self.expressions.gen_expr(ret_value, ret_type)
                        return self._make_return(
                            indent, f"::tpy::to_ptr_variant({ret_expr})")
                    # Narrowed variable or concrete lvalue: take address for implicit
                    # variant<T*...> construction
                    ret_expr = self.expressions.gen_expr(ret_value, ret_type)
                    return self._make_return(indent, f"&({ret_expr})")
                # When returning an error_return call from a matching
                # @error_return function, pass the std::expected through
                # directly -- no unwrap+rewrap needed.
                if self.ctx.current_error_return and self._get_error_return_fi(ret_value):
                    ret_expr = self._gen_error_return_call(ret_value)
                    return self._make_return(indent, ret_expr)
                # Own[abstract @dynamic P] return: mirror the function-call
                # arg path so `return Parrot(...)` lands in a `unique_ptr<Pet>`
                # slot via `std::make_unique<Adapter<P, Concrete>>(...)`.
                # `_is_dyn_own_wrap_needed` returns False for the forward case
                # so named locals/params keep C++ implicit-move on return.
                if (isinstance(ret_type, OwnType)
                        and self.expressions._is_dyn_own_wrap_needed(ret_value, ret_type)):
                    dyn_own_ret = self.expressions._gen_dynamic_protocol_arg(ret_value, ret_type)
                    if dyn_own_ret is not None:
                        return self._make_return(indent, dyn_own_ret)
                ret_expr = self.expressions.gen_expr(
                    ret_value, ret_type)
                # OPTIONAL_STORAGE source names (Own[Opt[P_ref]] params)
                # are rendered as std::optional<P>, not P*. When the return
                # type is the same Own[Optional[P_ref]] shape, std::move the
                # whole optional rather than dereffing -- (*x) is UB on a
                # nullopt and would also lose the None case.
                if (isinstance(ret_value, TpyName)
                        and self.ctx.needs_optional_to_ptr_lift(ret_value.name)
                        and is_own_pointer_repr_optional(ret_type)):
                    ret_expr = self.expressions._maybe_move(ret_value, ret_expr)
                # Dereference pointer-locals/pointer-globals on return (T* -> T&)
                elif self.ctx.is_indirect_name(ret_value):
                    ret_expr = f"(*{ret_expr})"
                    ret_expr = self.expressions._maybe_move(ret_value, ret_expr)
                # Unwrap value-optional expressions when return type is non-Optional.
                # The sema narrows the type inside `if x is not None:` branches,
                # but the C++ variable/field is still std::optional<T>.
                elif (
                    not isinstance(ret_type, OptionalType)
                    and self._is_value_optional_expr(ret_value)
                ):
                    analyzed_type = self.ctx.get_expr_type(ret_value)
                    if isinstance(analyzed_type, OptionalType):
                        ret_expr = f"::tpy::deref_optional_check({ret_expr})"
                        ret_expr = self.expressions._maybe_move(ret_value, ret_expr)
                    else:
                        ret_expr = f"(*{ret_expr})"
                        ret_expr = self.expressions._maybe_move(ret_value, ret_expr)
                        # Narrowed Optional[str] param: (*s) yields string_view
                        if (is_str_type(ret_type)
                                and self._is_optional_str_param(ret_value)):
                            ret_expr = f"std::string({ret_expr})"
                # StrView local returned as str needs explicit conversion.
                # Also wrap str-typed params, ternary/or of params/views
                # (all produce string_view in C++ despite being typed as str in sema).
                elif is_str_type(ret_type):
                    if (self._is_str_view_source(ret_value)
                            or self._expr_uses_optional_str_param(ret_value)):
                        ret_expr = f"std::string({ret_expr})"
                # Consuming method: move self fields on return (this->field is lvalue)
                if (self.ctx.in_consuming_method
                        and isinstance(ret_value, TpyFieldAccess)
                        and isinstance(ret_value.obj, TpyName)
                        and ret_value.obj.name == "self"):
                    ret_expr = f"std::move({ret_expr})"
                return self._make_return(indent, ret_expr)
            if self.ctx.current_error_return:
                return self._make_return(indent, "{}")
            return self._make_return(indent)
        elif isinstance(stmt, TpyBreak):
            return self._make_break_continue(indent, is_break=True)
        elif isinstance(stmt, TpyContinue):
            return self._make_break_continue(indent, is_break=False)
        elif isinstance(stmt, TpyPassStmt):
            return ""  # No-op - emit nothing
        elif isinstance(stmt, TpyGlobal):
            return ""  # No C++ output -- just a sema directive
        elif isinstance(stmt, TpyNonlocal):
            return ""  # No C++ output -- capture mode handles it
        elif isinstance(stmt, TpyRaise):
            return self._gen_raise(stmt, indent)
        elif isinstance(stmt, TpyImport):
            # Only emit __tpy_init() for user modules that have runtime init.
            # Skip builtins (no .cpp) and native_module (binding-only, no .cpp).
            module_info = self.ctx.analyzer.registry.get_module(stmt.module_name)
            skip_init = module_info and (module_info.is_builtin or module_info.is_native_module)
            result = ""
            if (module_info is not None
                    and module_info.is_native_module
                    and stmt.module_name in self.ctx.user_module_imports):
                # Native facades have no __tpy_init() of their own; chain into
                # the non-native source modules of any re-exported variables.
                for reached in self._native_facade_init_targets(stmt.module_name):
                    if reached in self.ctx.emitted_tpy_inits:
                        continue
                    result += f"{indent}{qualified_cpp_name(reached, '__tpy_init')}();\n"
                    self.ctx.emitted_tpy_inits.add(reached)
            if stmt.module_name in self.ctx.user_module_imports and not skip_init:
                # For dotted imports, emit parent package inits first (Python semantics)
                # e.g., "mypackage.utils" -> init mypackage first, then mypackage.utils
                parts = stmt.module_name.split('.')
                for i in range(1, len(parts)):
                    parent_pkg = '.'.join(parts[:i])
                    if parent_pkg == self.ctx.module_name:
                        continue  # don't self-init
                    if parent_pkg in self.ctx.all_user_modules and parent_pkg not in self.ctx.emitted_tpy_inits:
                        result += f"{indent}{qualified_cpp_name(parent_pkg, '__tpy_init')}();\n"
                        self.ctx.emitted_tpy_inits.add(parent_pkg)
                # Then init the submodule itself (skip self-init)
                if stmt.module_name != self.ctx.module_name and stmt.module_name not in self.ctx.emitted_tpy_inits:
                    result += f"{indent}{qualified_cpp_name(stmt.module_name, '__tpy_init')}();\n"
                    self.ctx.emitted_tpy_inits.add(stmt.module_name)
            return result
        return None

    def _native_facade_init_targets(self, native_module: str) -> list[str]:
        """Defining modules of variables re-exported by ``native_module``.

        Records, functions, and protocols re-exported by the facade are pure
        declarations; only re-exported variables involve runtime
        construction the consumer must trigger.

        The attribute table's VARIABLE bindings carry the chain-flattened
        ultimate definer in `binding.defining_module`, so a single pass
        over the facade's table yields the set of init targets.
        """
        registry = self.ctx.analyzer.registry
        info = registry.get_module(native_module)
        if info is None or info.module_attributes is None:
            return []
        order: list[str] = []
        seen: set[str] = set()
        for cell in info.module_attributes.values():
            bd = cell.binding
            if bd.kind != SymbolKind.VARIABLE or bd.defining_module is None:
                continue
            ult_mod = bd.defining_module
            if ult_mod in seen:
                continue
            ult_info = registry.get_module(ult_mod)
            if ult_info is None or ult_info.is_builtin or ult_info.is_native_module:
                continue
            seen.add(ult_mod)
            order.append(ult_mod)
        return order

    def _is_plain_nonvalue(self, t: TpyType) -> bool:
        """True for non-value types that need indirection (list, dict, record, etc.).

        Unwraps Own[T] and excludes pointer-repr Optional and Union which
        have their own codegen paths. Recursive union wrappers are value types.
        """
        check = t.wrapped if isinstance(t, OwnType) else t
        if check.is_value_type():
            return False
        if isinstance(check, OptionalType) and check.uses_pointer_repr():
            return False
        if self.ctx.is_ptr_variant_union(check):
            return False
        # Recursive union wrapper structs are value types
        if self.ctx.is_recursive_union(check):
            return False
        return True

    def _needs_indirection(self, target_type: TpyType | None, name: str,
                            init: TpyExpr | None) -> bool:
        """Check if a variable needs indirection (T* pointer-local or T& reference).

        Returns True when the variable is reassigned later or initialized from
        a non-rvalue (sharing/aliasing). The caller distinguishes T* vs T&.
        Optional non-value types always need indirection (they are nullable pointers).
        """
        if target_type is None:
            return False
        # Optional[T] for non-value T is always a pointer-local
        if isinstance(target_type, OptionalType) and target_type.uses_pointer_repr():
            return True
        if not self._is_plain_nonvalue(target_type):
            return False
        if name in self.ctx.move_through_vars:
            return False
        if name in self.ctx.reassigned_vars:
            return True
        if name in self.ctx.hoisted_vars:
            return True
        if init is not None and not self.ctx.is_rvalue_source(init):
            return True
        return False

    def _is_const_indirect(self, target_type: TpyType | None, init: TpyExpr | None,
                           stmt: 'TpyVarDecl | None' = None) -> bool:
        """Check if a local variable should use const indirection (const T* or const T&).

        Detects when the variable is derived from a ReadonlyType source:
        - Optional inner is ReadonlyType (None-seeded from readonly param)
        - Init expression has ReadonlyType in sema (direct alias of readonly param)
        - Sema var_types holds ReadonlyType for annotated locals that are later
          reassigned from a readonly source
        """
        if isinstance(target_type, OptionalType) and isinstance(target_type.inner, ReadonlyType):
            return True
        if init is not None:
            sema_type = self.ctx.analyzer.get_expr_type(init)
            if isinstance(sema_type, ReadonlyType):
                return True
        # For annotated Optional locals, sema var_types may hold
        # OptionalType(ReadonlyType(T)) even when stmt.type is plain Optional[T].
        if stmt is not None:
            sema_var_type = self.ctx.analyzer.var_types.get(id(stmt))
            if (isinstance(sema_var_type, OptionalType)
                    and isinstance(sema_var_type.inner, ReadonlyType)):
                return True
        # Readonly method call returns const T& -> variable needs const indirection.
        # (TypeParamRef returns are handled separately via val_or_cref_t in _gen_local_var_decl.)
        if isinstance(init, TpyMethodCall):
            fi = init.resolved_function_info
            if fi is not None and fi.is_readonly and self.ctx._call_returns_cpp_ref(fi, init.obj):
                return True
        return False

    def _is_dynamic_protocol_type(self, target_type: TpyType | None) -> bool:
        """Check if the type is a @dynamic protocol (needs adapter slot codegen)."""
        if target_type is None or not is_protocol_type(target_type):
            return False
        protocol_info = protocol_info_of(target_type)
        return protocol_info is not None and protocol_info.is_dynamic

    def _get_nullproto_constexpr_guards(self, condition: TpyExpr) -> list[str]:
        """Get param names that need if constexpr guards for nullable protocol params.

        When a nullable static protocol param is narrowed (e.g. `if items is not None:`),
        the narrowing body must be wrapped in `if constexpr (!std::same_as<T_X, std::nullptr_t>)`
        to prevent instantiation of protocol operations on nullptr_t.
        """
        guards = []
        match = match_is_none(condition)
        if match is not None:
            var_name, is_not_none = match
            if is_not_none and '.' not in var_name:
                declared = self.ctx.current_func_params.get(var_name)
                if declared and self.protocols.is_static_protocol_param(declared):
                    infos = self.protocols.get_all_protocol_params([(var_name, declared)])
                    if infos and infos[0].has_none:
                        guards.append(var_name)
        return guards

    def _is_protocol_isinstance_condition(self, condition: 'TpyExpr') -> bool:
        """Check if condition is isinstance(x, Protocol) requiring if constexpr."""
        if isinstance(condition, TpyCall) and condition.isinstance_is_protocol:
            return True
        if isinstance(condition, TpyUnaryOp) and condition.op == "!":
            return self._is_protocol_isinstance_condition(condition.operand)
        return False

    def _gen_dynamic_protocol_init(self, name: str, target_type: NominalType,
                                    init: 'TpyExpr', indent: str) -> str:
        """Generate slot + pointer-local for a @dynamic protocol variable.

        If the concrete type directly inherits the protocol base, emit a plain
        concrete slot (no adapter). Otherwise use adapter wrapping.
        If the init is already an erased protocol variable, just copy the pointer.
        Uses brace init to avoid C++ most-vexing-parse with constructor calls.
        """
        concrete_type = self.ctx.get_expr_type(init)
        base_type = self.protocols.get_dynamic_base_name(target_type)

        if is_protocol_type(concrete_type):
            # Already erased -- copy the pointer
            init_expr = self.expressions.gen_expr_deref(init, concrete_type)
            return f"{indent}{base_type}* {name} = &{init_expr};\n"

        concrete_cpp = self.types.type_to_cpp(concrete_type)
        init_slot = self.ctx.slots.next_slot()
        init_expr = self.expressions.gen_expr(init, concrete_type)

        if self.protocols.directly_implements_dynamic(concrete_type, target_type):
            # Direct inheritance -- plain concrete slot, implicit upcast
            slot_type = concrete_cpp
        else:
            # Structural conformance -- adapter wrapping
            slot_type = self.protocols.get_dynamic_adapter_type(target_type, concrete_cpp)

        return (f"{indent}{slot_type} {init_slot}{{{init_expr}}};\n"
                f"{indent}{base_type}* {name} = &{init_slot};\n")

    def _gen_dynamic_protocol_rebind(self, name: str, target_type: NominalType,
                                      init: 'TpyExpr', indent: str) -> str:
        """Generate slot rebind for a @dynamic protocol variable reassignment.

        Slots are hoisted to function scope via pending_hoist_decls so they
        survive block scopes (if/else branches, loops).
        """
        concrete_type = self.ctx.get_expr_type(init)

        if is_protocol_type(concrete_type):
            # Already erased -- rebind pointer to same object
            init_expr = self.expressions.gen_expr_deref(init, concrete_type)
            return f"{indent}{name} = &{init_expr};\n"

        concrete_cpp = self.types.type_to_cpp(concrete_type)
        rebind_slot = self.ctx.slots.next_slot()
        init_expr = self.expressions.gen_expr(init, concrete_type)

        if self.protocols.directly_implements_dynamic(concrete_type, target_type):
            slot_type = concrete_cpp
        else:
            slot_type = self.protocols.get_dynamic_adapter_type(target_type, concrete_cpp)

        # Hoist slot to function scope (survives block scopes).
        # Global scope (__tpy_init) needs 'static' so slots outlive the function.
        static_kw = "static " if self.ctx.slots.global_scope else ""
        self.ctx.pending_hoist_decls.append(
            f"{static_kw}std::optional<{slot_type}> {rebind_slot};\n"
        )
        return (f"{indent}{rebind_slot}.emplace({init_expr});\n"
                f"{indent}{name} = &*{rebind_slot};\n")

    def _resolve_literal_view_storage(self, name: str, var_type: TpyType) -> TpyType:
        """Substitute LiteralType[str/bytes] with the view-inferred storage form.

        Sema preserves `stmt.type = LiteralType` so OOS / dispatch / narrowing
        see the annotation; codegen needs the str/bytes view-vs-owned form
        chosen by view inference, looked up by variable name.
        """
        if not isinstance(var_type, LiteralType):
            return var_type
        family = view_family_for_type(var_type)
        if family is None:
            return var_type
        var_id = self.ctx.analyzer.ctx.view_var_map(family).get(name)
        if var_id is None:
            return var_type
        return self.types._resolve_view_storage(family, var_id)

    def _resolve_target_type(self, stmt: TpyVarDecl) -> TpyType | None:
        """Resolve the target type for a variable declaration."""
        target_type = resolve_stmt_binding_type(
            stmt,
            self.ctx.analyzer,
            include_global_binding=(self.ctx.current_ns is self.ctx.analyzer.global_ns),
        )
        if target_type is None and stmt.init:
            target_type = self.ctx.analyzer.get_expr_type(stmt.init)
        if target_type is not None:
            # Strip Ref and ReadonlyType -- C++ reference semantics are
            # handled by codegen binding (T& / auto&), not by the type itself.
            target_type = unwrap_readonly(unwrap_ref_type(target_type))
            if isinstance(target_type, OwnType):
                target_type = target_type.wrapped
            target_type = resolve_int_literals(target_type, self.ctx.analyzer.ctx.default_int_for_literal)
            if isinstance(target_type, FloatLiteralType):
                target_type = FLOAT
            resolved = self._resolve_pending_container(target_type)
            if resolved is not None:
                target_type = resolved
            elif isinstance(target_type, PendingViewType):
                target_type = self.types._resolve_pending_view(target_type)
        return target_type

    def _resolve_pending_container(self, typ: TpyType) -> TpyType | None:
        """Resolve PendingListType/PendingDictType/PendingSetType to their resolved concrete type.

        Returns None if the type is not a pending container or has no resolution yet.
        Uses the unified lookup on SemanticContext so adding a new container type
        is handled automatically.
        """
        if not isinstance(typ, PENDING_CONTAINER_TYPES):
            return None
        info = self.ctx.analyzer.ctx.get_container_info(typ.literal_id)
        if info and info.resolved_type:
            return info.resolved_type
        return None

    def _normalize_decl_type_for_cpp(self, var_type: TpyType) -> TpyType:
        """Normalize declaration type before C++ emission."""
        var_type = unwrap_ref_type(var_type)
        resolve_lit = self.ctx.analyzer.ctx.default_int_for_literal
        resolved = self._resolve_pending_container(var_type)
        if resolved is not None:
            var_type = resolved
        elif isinstance(var_type, PendingListType):
            # Fallback for unresolved list: resolve IntLiteralType in element
            elem = var_type.element_type
            if isinstance(elem, IntLiteralType):
                var_type = make_list(resolve_lit(elem))
        elif isinstance(var_type, PendingViewType):
            var_type = self.types._resolve_pending_view(var_type)
        # Resolve IntLiteralType in all composite types (tuples, arrays, lists)
        var_type = resolve_int_literals(var_type, resolve_lit)
        # Resolve FloatLiteralType to float64 (same as sema: float literals default to double)
        if isinstance(var_type, FloatLiteralType):
            var_type = FLOAT
        # Optional non-value types use inner type (pointer-local adds T*)
        if isinstance(var_type, OptionalType) and var_type.uses_pointer_repr():
            var_type = var_type.inner
        return var_type

    def _cpp_decl_type(self, var_type: TpyType) -> str:
        """Return C++ declaration type name for a normalized semantic type."""
        normalized = self._normalize_decl_type_for_cpp(var_type)
        if self.ctx.contains_protocol_type(normalized):
            return "auto"
        if isinstance(normalized, TupleType) and normalized.has_ref_elements():
            return "auto"
        return self.types.type_to_cpp(normalized)

    def _resolve_cpp_type(self, stmt: TpyVarDecl) -> str:
        """Resolve the C++ type string for a variable declaration."""
        if stmt.type:
            return self._cpp_decl_type(self._resolve_literal_view_storage(stmt.name, stmt.type))
        elif stmt.init:
            resolved_type = resolve_stmt_binding_type(
                stmt,
                self.ctx.analyzer,
                include_global_binding=(self.ctx.current_ns is self.ctx.analyzer.global_ns),
            )
            if resolved_type is None or isinstance(resolved_type, (*PENDING_CONTAINER_TYPES, PendingViewType)):
                resolved_type = self.ctx.get_expr_type(stmt.init)
            if resolved_type is None:
                raise CodeGenError(
                    f"Could not infer type for variable '{stmt.name}'", loc=stmt.loc
                )
            return self._cpp_decl_type(resolved_type)
        raise CodeGenError(f"Variable '{stmt.name}' has no type annotation and no initializer", loc=stmt.loc)

    # --- Rvalue slot helpers (shared by init and rebind) ---

    @staticmethod
    def _ptr_from_rvalue_slot(slot: str, init_expr: str, is_opt_field: bool,
                              is_optional_slot: bool = True) -> str:
        """Assign rvalue into pre-declared slot and derive pointer expression."""
        if is_opt_field:
            return f"::tpy::optional_to_ptr({slot} = {init_expr})"
        if is_optional_slot:
            return f"&*({slot} = {init_expr})"
        return f"&({slot} = {init_expr})"

    @staticmethod
    def _ptr_from_local_slot(slot: str, is_opt_field: bool) -> str:
        """Derive pointer from an inline-declared slot."""
        if is_opt_field:
            return f"::tpy::optional_to_ptr({slot})"
        return f"&{slot}"

    @staticmethod
    def _slot_decl_type(cpp_type: str, is_opt_field: bool) -> str:
        """C++ type for a rvalue materialization slot."""
        return f"std::optional<{cpp_type}>" if is_opt_field else cpp_type

    def _gen_pointer_local_init(self, name: str, cpp_type: str, init: 'TpyExpr',
                                target_type: TpyType | None, indent: str) -> str:
        """Generate pointer-local initialization code.

        Classifies the source expression:
        - None literal -> nullptr
        - OptionalType source (function returning T*) -> direct pointer copy
        - rvalue -> new slot + take address
        - pointer-local name -> pointer copy
        - lvalue ref (param, subscript, field) -> take address

        For vars with future rvalue rebinds, a separate rebind slot is
        pre-declared so aliases to the init value aren't overwritten.
        """
        from ..parse import TpyName as _TpyName
        name = escape_cpp_name(name)
        const_pfx = "const " if name in self.ctx.const_indirect_locals else ""

        # None literal -> nullptr (Optional/Ptr) or monostate slot (Union)
        if isinstance(init, TpyNoneLiteral):
            # Union pointer-locals: allocate slot with std::monostate{}
            if isinstance(target_type, UnionType):
                init_expr = "std::monostate{}"
                static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
                hoist_static_kw = "static " if self.ctx.slots.global_scope else ""
                slot_opt_cpp = f"std::optional<{cpp_type}>"
                init_slot = self.ctx.slots.next_slot()
                is_hoisted = name in self.ctx.hoisted_vars or name in self.ctx.branch_hoisted_vars
                if is_hoisted:
                    self.ctx.pending_hoist_decls.append(f"{hoist_static_kw}{slot_opt_cpp} {init_slot};\n")
                    if name in self.ctx.rvalue_reassigned_vars:
                        rebind_slot = self.ctx.slots.next_slot()
                        self.ctx.rebind_slots[name] = rebind_slot
                        self.ctx.pending_hoist_decls.append(f"{hoist_static_kw}{slot_opt_cpp} {rebind_slot};\n")
                    else:
                        self.ctx.rebind_slots[name] = init_slot
                    return f"{indent}{const_pfx}{cpp_type}* {name} = &({init_slot}.emplace({init_expr}));\n"
                if name in self.ctx.rvalue_reassigned_vars:
                    rebind_slot = self.ctx.slots.next_slot()
                    self.ctx.rebind_slots[name] = rebind_slot
                    return (f"{indent}{static_kw}{cpp_type} {init_slot} = {init_expr};\n"
                            f"{indent}{static_kw}{slot_opt_cpp} {rebind_slot};\n"
                            f"{indent}{const_pfx}{cpp_type}* {name} = &{init_slot};\n")
                self.ctx.rebind_slots[name] = init_slot
                return (f"{indent}{static_kw}{cpp_type} {init_slot} = {init_expr};\n"
                        f"{indent}{const_pfx}{cpp_type}* {name} = &{init_slot};\n")
            # Pre-declare rebind slot if future rvalue rebinds need it
            rebind_decl = ""
            if name in self.ctx.rvalue_reassigned_vars:
                static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
                hoist_static_kw = "static " if self.ctx.slots.global_scope else ""
                slot_opt_cpp = f"std::optional<{cpp_type}>"
                slot = self.ctx.slots.next_slot()
                self.ctx.rebind_slots[name] = slot
                if name in self.ctx.hoisted_vars:
                    self.ctx.pending_hoist_decls.append(f"{hoist_static_kw}{slot_opt_cpp} {slot};\n")
                else:
                    rebind_decl = f"{indent}{static_kw}{slot_opt_cpp} {slot};\n"
            return f"{rebind_decl}{indent}{const_pfx}{cpp_type}* {name} = nullptr;\n"

        # OPTIONAL_STORAGE source: an `Own[Opt[T_ref]]` storage-form
        # `optional<T>` value consumed as `T*`. Two source shapes share
        # the same `optional_to_ptr` lift but live at different lifetime
        # tiers:
        #   * rvalue call (callee returns `Own[Opt[T_ref]]`) -- the
        #     returned `optional<P>` has no other home, materialize a
        #     slot and lift the slot. Rebinds reuse `rebind_slots[name]`.
        #   * lvalue name (`Own[Opt[T_ref]]` param in `optional_locals`)
        #     -- the param itself is the storage; pure lift, no slot.
        # Dispatched structurally on AST shape so the param-source case
        # cannot route through the slot path (which would emit a
        # redundant `std::optional<P> __slot = x;` materialization that
        # regressed `cases/auto_move/scalar_own_optional` on a prior
        # attempt that gated both tiers on a single polymorphic predicate).
        # `callee_returns_own_ptr_optional` is already AST-aware (returns
        # False for non-call nodes), so no outer isinstance pre-guard.
        is_call_src = self.ctx.callee_returns_own_ptr_optional(init)
        is_name_src = (isinstance(init, TpyName)
                       and self.ctx.needs_optional_to_ptr_lift(init.name))
        if is_call_src or is_name_src:
            init_expr = self.expressions.gen_expr(init, target_type)
            if is_call_src:
                static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
                slot = self.ctx.slots.next_slot()
                self.ctx.rebind_slots[name] = slot
                slot_type = self._slot_decl_type(cpp_type, is_opt_field=True)
                deref = self._ptr_from_local_slot(slot, is_opt_field=True)
                return (f"{indent}{static_kw}{slot_type} {slot} = {init_expr};\n"
                        f"{indent}{const_pfx}{cpp_type}* {name} = {deref};\n")
            return f"{indent}{const_pfx}{cpp_type}* {name} = ::tpy::optional_to_ptr({init_expr});\n"

        init_type = self.ctx.get_expr_type(init)
        is_opt_field = (isinstance(init_type, OptionalType)
                        and init_type.uses_pointer_repr()
                        and isinstance(init, TpyFieldAccess))
        if isinstance(init_type, OptionalType) and init_type.uses_pointer_repr():
            # Storage-form Optional source (field, container subscript,
            # storage_form_optional_locals): lvalue lift via optional_to_ptr.
            # rvalue cases (e.g. rvalue field-access on a moved-from object)
            # fall through to the generic rvalue-slot path below.
            if (self.ctx.is_storage_form_optional_source(init)
                    and not self.ctx.is_rvalue_source(init)):
                # In const methods, field access yields const ref; propagate const
                # to the narrowed pointer so downstream dereferences are also const.
                # Same for loop-var / comp-unpack-var bound from a const-bound
                # storage source -- the iteration yields `const optional<P>&`,
                # so `optional_to_ptr` returns `const P*`.
                if not const_pfx and (
                        (isinstance(init, TpyFieldAccess)
                            and self._is_const_union_source(init))
                        or (isinstance(init, TpyName)
                            and init.name in self.ctx.const_storage_form_optional_locals)):
                    const_pfx = "const "
                    self.ctx.const_indirect_locals.add(name)
                init_expr = self.expressions.gen_expr(init, target_type)
                return f"{indent}{const_pfx}{cpp_type}* {name} = ::tpy::optional_to_ptr({init_expr});\n"
            # Value-emit rvalues reach this branch because sema annotates
            # them with the Optional target type, but their gen_expr emits
            # a value -- the direct-pointer assignment below would produce
            # `T* x = T-val`. Fall through to the rvalue-slot path.
            if not self.ctx.is_value_emit_rvalue(init):
                init_expr = self.expressions.gen_expr(init, target_type)
                return f"{indent}{const_pfx}{cpp_type}* {name} = {init_expr};\n"

        init_expr = self.expressions.gen_expr(init, target_type)

        is_hoisted = name in self.ctx.hoisted_vars or name in self.ctx.branch_hoisted_vars
        static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
        # Hoisted decls go to function scope -- use global_scope flag from slot state
        hoist_static_kw = "static " if self.ctx.slots.global_scope else ""
        slot_opt_cpp = "auto" if cpp_type == "auto" else f"std::optional<{cpp_type}>"
        target = f"{const_pfx}{cpp_type}* {name}"
        if self.ctx.is_rvalue_source(init):
            init_slot = self.ctx.slots.next_slot()
            slot_type = self._slot_decl_type(cpp_type, is_opt_field)
            if is_hoisted:
                self.ctx.pending_hoist_decls.append(f"{hoist_static_kw}{slot_opt_cpp} {init_slot};\n")
                if name in self.ctx.rvalue_reassigned_vars:
                    rebind_slot = self.ctx.slots.next_slot()
                    self.ctx.rebind_slots[name] = rebind_slot
                    self.ctx.pending_hoist_decls.append(f"{hoist_static_kw}{slot_opt_cpp} {rebind_slot};\n")
                else:
                    self.ctx.rebind_slots[name] = init_slot
                deref = self._ptr_from_rvalue_slot(init_slot, init_expr, is_opt_field,
                                                   init_slot not in self.ctx.plain_rebind_slots)
                return f"{indent}{target} = {deref};\n"
            if name in self.ctx.rvalue_reassigned_vars:
                # Separate rebind slot so aliases to init value aren't overwritten
                rebind_slot = self.ctx.slots.next_slot()
                self.ctx.rebind_slots[name] = rebind_slot
                deref = self._ptr_from_local_slot(init_slot, is_opt_field)
                return (f"{indent}{static_kw}{slot_type} {init_slot} = {init_expr};\n"
                        f"{indent}{static_kw}{slot_opt_cpp} {rebind_slot};\n"
                        f"{indent}{target} = {deref};\n")
            self.ctx.rebind_slots[name] = init_slot
            deref = self._ptr_from_local_slot(init_slot, is_opt_field)
            return (f"{indent}{static_kw}{slot_type} {init_slot} = {init_expr};\n"
                    f"{indent}{target} = {deref};\n")

        # Pre-declare rebind slot for lvalue-init vars with future rvalue rebinds
        rebind_decl = ""
        if name in self.ctx.rvalue_reassigned_vars:
            slot = self.ctx.slots.next_slot()
            self.ctx.rebind_slots[name] = slot
            if is_hoisted:
                self.ctx.pending_hoist_decls.append(f"{hoist_static_kw}{slot_opt_cpp} {slot};\n")
            else:
                rebind_decl = f"{indent}{static_kw}{slot_opt_cpp} {slot};\n"

        if isinstance(init, _TpyName) and init.name in self.ctx.pointer_locals:
            return f"{rebind_decl}{indent}{const_pfx}{cpp_type}* {name} = {init_expr};\n"
        elif self.ctx._is_pointer_global(init):
            return f"{rebind_decl}{indent}{const_pfx}{cpp_type}* {name} = {init_expr};\n"
        elif self.ctx.is_global_name(init):
            return f"{rebind_decl}{indent}{const_pfx}{cpp_type}* {name} = &({init_expr});\n"
        else:
            # lvalue ref: param, subscript, field -> take address
            return f"{rebind_decl}{indent}{const_pfx}{cpp_type}* {name} = &({init_expr});\n"

    def _gen_slice_assign(self, stmt: TpyAssign, indent: str) -> str:
        """Generate code for slice assignment via __setitem__(basic_slice/slice) stub dispatch."""
        assert isinstance(stmt.target, TpySubscript)
        sl = stmt.target.index
        assert isinstance(sl, TpySlice)
        fi = stmt.target.slice_function_info
        assert fi is not None
        obj = self.expressions.gen_expr(stmt.target.obj)
        subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(stmt.target.obj) else obj
        slice_arg = self.expressions._gen_slice_object(sl, stepped=stmt.target.is_stepped_slice)
        target_type = self.ctx.get_expr_type(stmt.target)
        value = self.expressions.gen_expr(stmt.value, target_type)
        value = self.expressions._maybe_move(stmt.value, value)
        # Non-empty array literals generate bare {e1, e2, ...} which C++ can't deduce Range from;
        # empty literals already include the explicit type from _gen_array_literal.
        if isinstance(stmt.value, TpyArrayLiteral) and stmt.value.elements:
            assert is_list(target_type)
            elem_cpp = self.types.type_to_cpp(target_type.type_args[0])
            value = f"std::vector<{elem_cpp}>{value}"
        code = self.builtins.gen_call_from_fi(fi, subscript_obj, [slice_arg, value])
        return f"{indent}{code};\n"

    def _gen_pointer_local_rebind(self, name: str, cpp_type: str, init: 'TpyExpr',
                                   target_type: TpyType | None, indent: str) -> str:
        """Generate pointer-local rebinding code (reassignment).

        For rvalue sources, reuses the rebind slot declared at init site
        to avoid creating loop-scoped storage that would dangle.
        """
        from ..parse import TpyName as _TpyName
        cpp_name = escape_cpp_name(name)

        # None literal -> set to nullptr (Optional/Ptr) or monostate (Union)
        if isinstance(init, TpyNoneLiteral):
            if isinstance(target_type, UnionType):
                rebind_slot = self.ctx.rebind_slots.get(name)
                if rebind_slot:
                    is_optional_slot = rebind_slot not in self.ctx.plain_rebind_slots
                    if is_optional_slot:
                        return (f"{indent}{rebind_slot}.emplace(std::monostate{{}});\n"
                                f"{indent}{cpp_name} = &(*{rebind_slot});\n")
                    return (f"{indent}{rebind_slot} = std::monostate{{}};\n"
                            f"{indent}{cpp_name} = &{rebind_slot};\n")
                return f"{indent}(*{cpp_name}) = std::monostate{{}};\n"
            return f"{indent}{cpp_name} = nullptr;\n"

        # OPTIONAL_STORAGE source: see the parallel handler in
        # _gen_pointer_local_init for the design rationale. rvalue call
        # reuses the slot declared at init site; lvalue name lifts
        # directly. Structural AST-shape dispatch keeps the param-source
        # case off the slot path. If the call-source path finds no slot
        # (defensive -- the init-site path always declares one), falls
        # through to the generic rvalue path below.
        is_call_src = self.ctx.callee_returns_own_ptr_optional(init)
        is_name_src = (isinstance(init, TpyName)
                       and self.ctx.needs_optional_to_ptr_lift(init.name))
        if is_call_src:
            rebind_slot = self.ctx.rebind_slots.get(name)
            if rebind_slot is not None:
                init_expr = self.expressions.gen_expr(init, target_type)
                return (f"{indent}{rebind_slot} = {init_expr};\n"
                        f"{indent}{cpp_name} = ::tpy::optional_to_ptr({rebind_slot});\n")
        elif is_name_src:
            init_expr = self.expressions.gen_expr(init, target_type)
            return f"{indent}{cpp_name} = ::tpy::optional_to_ptr({init_expr});\n"

        init_type = self.ctx.get_expr_type(init)
        # Optional non-value field on lvalue -> optional_to_ptr directly
        # Optional non-value non-field source -> T* pass-through
        # Optional non-value field on rvalue -> falls through to rvalue path
        is_storage_opt_lvalue = (
            self.ctx.is_storage_form_optional_source(init)
            and not self.ctx.is_rvalue_source(init))
        is_opt_field = (isinstance(init_type, OptionalType)
                        and init_type.uses_pointer_repr()
                        and (isinstance(init, TpyFieldAccess)
                             or is_storage_opt_lvalue))
        if isinstance(init_type, OptionalType) and init_type.uses_pointer_repr():
            if is_storage_opt_lvalue:
                init_expr = self.expressions.gen_expr(init, target_type)
                return f"{indent}{cpp_name} = ::tpy::optional_to_ptr({init_expr});\n"
            # Mirror of the init-site value-emit-rvalue check. Currently
            # unreachable for container literals because sema infers their
            # intrinsic type during rebind (not the target Optional), but
            # kept symmetric in case sema's type propagation changes.
            if not self.ctx.is_value_emit_rvalue(init):
                init_expr = self.expressions.gen_expr(init, target_type)
                return f"{indent}{cpp_name} = {init_expr};\n"

        init_expr = self.expressions.gen_expr(init, target_type)

        is_hoisted = name in self.ctx.hoisted_vars or name in self.ctx.branch_hoisted_vars
        static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
        hoist_static_kw = "static " if self.ctx.slots.global_scope else ""
        slot_opt_cpp = "auto" if cpp_type == "auto" else f"std::optional<{cpp_type}>"
        if self.ctx.is_rvalue_source(init):
            rebind_slot = self.ctx.rebind_slots.get(name)
            if rebind_slot:
                deref = self._ptr_from_rvalue_slot(rebind_slot, init_expr, is_opt_field,
                                                   rebind_slot not in self.ctx.plain_rebind_slots)
                return f"{indent}{cpp_name} = {deref};\n"
            # First rvalue assignment (e.g. global init) -- declare slot here
            slot = self.ctx.slots.next_slot()
            self.ctx.rebind_slots[name] = slot
            slot_type = self._slot_decl_type(cpp_type, is_opt_field)
            if is_hoisted:
                self.ctx.pending_hoist_decls.append(f"{hoist_static_kw}{slot_opt_cpp} {slot};\n")
                deref = self._ptr_from_rvalue_slot(slot, init_expr, is_opt_field,
                                                   slot not in self.ctx.plain_rebind_slots)
                return f"{indent}{cpp_name} = {deref};\n"
            if not is_opt_field:
                self.ctx.plain_rebind_slots.add(slot)
            deref = self._ptr_from_local_slot(slot, is_opt_field)
            return (f"{indent}{static_kw}{slot_type} {slot} = {init_expr};\n"
                    f"{indent}{cpp_name} = {deref};\n")
        elif isinstance(init, _TpyName) and init.name in self.ctx.pointer_locals:
            return f"{indent}{cpp_name} = {init_expr};\n"
        elif self.ctx._is_pointer_global(init):
            return f"{indent}{cpp_name} = {init_expr};\n"
        elif self.ctx.is_global_name(init):
            return f"{indent}{cpp_name} = &({init_expr});\n"
        else:
            return f"{indent}{cpp_name} = &({init_expr});\n"

    def _is_const_union_source(self, expr: TpyExpr) -> bool:
        """Check if an expression yields a const value-variant (needs to_const_ptr_variant)."""
        if isinstance(expr, TpyCoerce):
            return self._is_const_union_source(expr.expr)
        if isinstance(expr, (TpyFieldAccess, TpySubscript)):
            obj = expr.obj
            if isinstance(obj, TpyName):
                return (obj.name in self.ctx.const_ref_params
                        or obj.name in self.ctx.const_indirect_locals)
            # Chained access (outer.inner.pet): recurse on the object
            return self._is_const_union_source(obj)
        return False

    def _gen_ptr_variant_local_init(
        self, stmt: 'TpyVarDecl', target_type: UnionType, cpp_name: str, indent: str,
    ) -> str:
        """Generate initialization for a pointer-variant union local.

        Handles three source kinds:
        - Pointer-variant source (param, local, function return) -> copy directly
        - Rvalue (constructor, Own return) -> storage slot + to_ptr_variant
        - Lvalue value-variant (field, container element) -> to_ptr_variant
        - None literal -> std::monostate{}
        """
        self.ctx.ptr_variant_locals.add(stmt.name)
        pv_type = self.types.type_to_cpp_ptr_variant(target_type)
        val_type = self.types.type_to_cpp(target_type)

        if not stmt.init:
            # Uninitialized nullable union -> monostate
            return f"{indent}{pv_type} {cpp_name} = std::monostate{{}};\n"

        if isinstance(stmt.init, TpyNoneLiteral):
            return f"{indent}{pv_type} {cpp_name} = std::monostate{{}};\n"

        if self.ctx.is_ptr_variant_source(stmt.init):
            # Already a pointer variant (param, local, function call returning ptr variant)
            init_expr = self.expressions.gen_expr(stmt.init, target_type)
            return f"{indent}{pv_type} {cpp_name} = {init_expr};\n"

        if self.ctx.is_rvalue_source(stmt.init):
            # Rvalue (constructor, Own return, literal) -> allocate storage slot
            static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
            init_expr = self.expressions.gen_expr(stmt.init, target_type)
            slot = self.ctx.slots.next_slot()
            # Pre-declare rebind slot if the variable gets reassigned later with rvalues
            rebind_decl = ""
            if stmt.name in self.ctx.rvalue_reassigned_vars:
                rebind_slot = self.ctx.slots.next_slot()
                self.ctx.rebind_slots[stmt.name] = rebind_slot
                rebind_decl = f"{indent}{static_kw}std::optional<{val_type}> {rebind_slot};\n"
            return (f"{rebind_decl}"
                    f"{indent}{static_kw}{val_type} {slot} = {init_expr};\n"
                    f"{indent}{pv_type} {cpp_name} = ::tpy::to_ptr_variant({slot});\n")

        # Lvalue source: either a value-variant lvalue or a concrete-type lvalue
        init_type = self.ctx.get_expr_type(stmt.init)
        init_expr = self.expressions.gen_expr(stmt.init, target_type)
        # Pre-declare rebind slot if needed
        rebind_decl = ""
        if stmt.name in self.ctx.rvalue_reassigned_vars:
            static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
            rebind_slot = self.ctx.slots.next_slot()
            self.ctx.rebind_slots[stmt.name] = rebind_slot
            rebind_decl = f"{indent}{static_kw}std::optional<{val_type}> {rebind_slot};\n"
        # Detect const source (field on const-ref param or const-indirect local)
        is_const_source = self._is_const_union_source(stmt.init)
        # If source is a value variant (field, container element), convert to pointer variant
        if isinstance(init_type, UnionType):
            if is_const_source:
                cpv_type = self.types.type_to_cpp_const_ptr_variant(target_type)
                self.ctx.const_indirect_locals.add(stmt.name)
                return (f"{rebind_decl}"
                        f"{indent}{cpv_type} {cpp_name} = ::tpy::to_const_ptr_variant({init_expr});\n")
            return (f"{rebind_decl}"
                    f"{indent}{pv_type} {cpp_name} = ::tpy::to_ptr_variant({init_expr});\n")
        # Concrete-type lvalue (e.g. Dog param): take address for implicit variant construction
        if is_const_source:
            cpv_type = self.types.type_to_cpp_const_ptr_variant(target_type)
            self.ctx.const_indirect_locals.add(stmt.name)
            return (f"{rebind_decl}"
                    f"{indent}{cpv_type} {cpp_name}{{&({init_expr})}};\n")
        return (f"{rebind_decl}"
                f"{indent}{pv_type} {cpp_name}{{&({init_expr})}};\n")

    def _gen_ptr_variant_local_reassign(
        self, stmt: 'TpyVarDecl', target_type: TpyType | None, cpp_name: str, indent: str,
    ) -> str:
        """Generate reassignment for a pointer-variant union local."""
        assert target_type is not None
        assert isinstance(target_type, UnionType)
        pv_type = self.types.type_to_cpp_ptr_variant(target_type)
        val_type = self.types.type_to_cpp(target_type)

        if isinstance(stmt.init, TpyNoneLiteral):
            return f"{indent}{cpp_name} = std::monostate{{}};\n"

        if self.ctx.is_ptr_variant_source(stmt.init):
            init_expr = self.expressions.gen_expr(stmt.init, target_type)
            return f"{indent}{cpp_name} = {init_expr};\n"

        if self.ctx.is_rvalue_source(stmt.init):
            # Rvalue -> use pre-declared rebind slot
            init_expr = self.expressions.gen_expr(stmt.init, target_type)
            rebind_slot = self.ctx.rebind_slots.get(stmt.name)
            if rebind_slot:
                return (f"{indent}{rebind_slot}.emplace({init_expr});\n"
                        f"{indent}{cpp_name} = ::tpy::to_ptr_variant(*{rebind_slot});\n")
            # Fallback: allocate inline slot
            static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
            slot = self.ctx.slots.next_slot()
            return (f"{indent}{static_kw}{val_type} {slot} = {init_expr};\n"
                    f"{indent}{cpp_name} = ::tpy::to_ptr_variant({slot});\n")

        # Lvalue source
        init_type = self.ctx.get_expr_type(stmt.init)
        init_expr = self.expressions.gen_expr(stmt.init, target_type)
        if isinstance(init_type, UnionType):
            return f"{indent}{cpp_name} = ::tpy::to_ptr_variant({init_expr});\n"
        # Concrete-type lvalue: take address
        pv_cpp = self.types.type_to_cpp_ptr_variant(target_type)
        return f"{indent}{cpp_name} = {pv_cpp}{{&({init_expr})}};\n"

    def _maybe_wrap_storage_tuple_source(self, stmt: 'TpyTupleUnpack', value_expr: str) -> str:
        """If unpacking a storage-form tuple into pointer-form Optional locals,
        wrap the source with tuple_to_pointer so std::get<I> yields T*.

        When the source binding is const (loop var iterating a const list /
        dict.values() / self.field in a readonly method), the inner pointers
        derived via optional_to_ptr come out as `const T*`, so the converted
        tuple type must use `to_cpp_return_const()` to match.
        """
        ptr_form = TupleType(tuple(stmt.target_types))
        if not ptr_form.has_pointer_repr_optional_element():
            return value_expr
        if not self.ctx.is_storage_form_source(stmt.value):
            return value_expr
        is_const_source = (isinstance(stmt.value, TpyName)
                           and stmt.value.name in self.ctx.const_storage_form_tuple_locals)
        cpp = (ptr_form.to_cpp_return_const() if is_const_source
               else ptr_form.to_cpp_return())
        return f"::tpy::tuple_to_pointer<{cpp}>({value_expr})"

    def gen_yield_value(self, yield_stmt: TpyYield) -> str:
        """Emit yield value, bridging storage->pointer when the source is a
        storage location and the iterator slot is borrow form.

        Iterator yields hand out references like function returns: the slot
        for `tuple[T | None, ...]` is `std::tuple<T*, ...>` (borrow form), so
        a storage-form source (field, subscript, etc.) needs `tuple_to_pointer`
        to bridge. Pointer-form sources (rvalue tuple literals, pointer-form
        locals) already match the slot and pass through unchanged.
        """
        yield_type = self.ctx.current_yield_type
        expr = self.expressions.gen_expr(yield_stmt.value, yield_type)
        return self._maybe_wrap_tuple_to_pointer(
            expr, yield_type, self.ctx.unwrap_copy(yield_stmt.value))

    def _maybe_wrap_tuple_to_pointer(self, expr: str, target_type: TpyType | None,
                                      source: TpyExpr | None = None) -> str:
        """Wrap a storage-form tuple expression with tuple_to_pointer if the
        target slot is std::tuple<T*, ...> (borrow form) and the source reads
        from a storage location. Mirror of `_maybe_wrap_tuple_to_storage`.
        """
        if target_type is None:
            return expr
        unwrapped = unwrap_readonly(unwrap_ref_type(target_type))
        if not (isinstance(unwrapped, TupleType)
                and unwrapped.has_pointer_repr_optional_element()):
            return expr
        if source is None or not self.ctx.is_storage_form_source(source):
            return expr
        return f"::tpy::tuple_to_pointer<{unwrapped.to_cpp_return()}>({expr})"

    def _maybe_wrap_tuple_to_storage(self, expr: str, target_type: TpyType | None,
                                       source: TpyExpr | None = None) -> str:
        """Wrap a pointer-form tuple expression with tuple_to_storage if the
        target's storage is std::tuple<std::optional<T>, ...>.

        Returns expr unchanged when target isn't a tuple containing pointer-
        repr Optional elements, or when `source` is itself a storage-form
        location (field, subscript, global, storage-form local) -- those
        already match the target's slot shape and need no conversion.
        """
        if target_type is None:
            return expr
        unwrapped = unwrap_readonly(unwrap_ref_type(target_type))
        if not (isinstance(unwrapped, TupleType)
                and unwrapped.has_pointer_repr_optional_element()):
            return expr
        if source is not None and self.ctx.is_storage_form_source(source):
            return expr
        return f"::tpy::tuple_to_storage<{unwrapped.to_cpp()}>({expr})"

    def _gen_var_decl_code(self, stmt: TpyVarDecl, indent: str) -> str | None:
        """Generate code for a variable declaration. Returns code to write or None."""
        from ..parse.nodes import VarLinkage
        if stmt.linkage != VarLinkage.DEFAULT:
            return None
        # Final globals are defined at namespace scope, skip in __tpy_init
        if stmt.is_final:
            return None

        # Generator body: variable is a struct field, emit assignment only
        if self.ctx.in_generator_body and stmt.name in self.ctx.generator_field_names:
            self.ctx.declared_vars.add(stmt.name)
            self.ctx.local_scope_names.add(stmt.name)
            if stmt.init:
                cpp_name = escape_cpp_name(stmt.name)
                # Generator Optional[T] field stores std::optional<std::optional<T>>:
                # outer = init-tracking, inner = the T | None storage form. A None
                # init must engage the outer with a default-constructed (nullopt)
                # inner -- assigning bare `nullptr` would be a type error and
                # `std::nullopt` would set the outer to nullopt instead of
                # engaging it.
                if (stmt.name in self.ctx.generator_optional_fields
                        and isinstance(stmt.init, TpyNoneLiteral)):
                    var_type = self.ctx.var_types.get(stmt.name)
                    if (isinstance(var_type, OptionalType)
                            and var_type.uses_pointer_repr()):
                        inner_cpp = var_type.to_cpp()
                        return f"{indent}{cpp_name} = {inner_cpp}{{}};\n"
                init_expr = self.expressions.gen_expr(stmt.init)
                return f"{indent}{cpp_name} = {init_expr};\n"
            return None

        cpp_name = escape_cpp_name(stmt.name)

        # Global-declared vars: emit assignment to the existing global, not a local decl
        if stmt.name in self.ctx.global_declared_vars:
            if not stmt.init:
                return None
            var_type = self.ctx.get_expr_type(stmt.init)
            init_expr = self.expressions.gen_expr(stmt.init, var_type)
            init_expr = self._maybe_wrap_tuple_to_storage(init_expr, var_type, stmt.init)
            target_name = self.ctx.native_global_names.get(stmt.name, stmt.name)
            return f"{indent}{target_name} = {init_expr};\n"

        # Check if variable is already declared (reassignment)
        if stmt.name in self.ctx.declared_vars:
            if stmt.init:
                var_type = self.ctx.var_types.get(stmt.name)
                form = self.ctx.local_cpp_form(stmt.name)
                if form is LocalCppForm.OPTIONAL_STORAGE:
                    # Hoisted optional<T>: move-assign into the slot.
                    init_expr = self.expressions.gen_expr(stmt.init, var_type)
                    return f"{indent}{cpp_name} = {init_expr};\n"
                if form is LocalCppForm.POINTER:
                    # @dynamic protocol reassignment: new adapter slot + rebind
                    if self._is_dynamic_protocol_type(var_type):
                        return self._gen_dynamic_protocol_rebind(stmt.name, var_type, stmt.init, indent)
                    # OptionalType uses inner type (pointer-local adds T*)
                    resolve_type = var_type
                    if isinstance(var_type, OptionalType) and var_type.uses_pointer_repr():
                        resolve_type = var_type.inner
                    cpp_type = self.types.type_to_cpp(resolve_type) if resolve_type else "auto"
                    # Structural protocol types map to C++ concepts which
                    # cannot be used as variable types; use auto instead.
                    # @dynamic protocols already have concrete base class
                    # names, so only check for structural protocols here.
                    if (resolve_type and isinstance(resolve_type, NominalType)
                            and resolve_type.is_protocol and not resolve_type.is_dynamic_protocol):
                        cpp_type = "auto"
                    return self._gen_pointer_local_rebind(stmt.name, cpp_type, stmt.init, var_type, indent)
                if form is LocalCppForm.PTR_VARIANT:
                    return self._gen_ptr_variant_local_reassign(stmt, var_type, cpp_name, indent)
                # String x = x + y -> x += y for buffer reuse
                if result := self._try_str_inplace_append(stmt.name, cpp_name, stmt.init, var_type, indent):
                    return result
                init_expr = self.expressions.gen_expr(stmt.init, var_type)
                init_expr = self._maybe_wrap_tuple_to_storage(init_expr, var_type, stmt.init)
                return f"{indent}{cpp_name} = {init_expr};\n"
            return None

        # Determine target type for first declaration
        target_type = self._resolve_target_type(stmt)

        # First declaration - track the type and mark as local (shadows globals)
        self.ctx.declared_vars.add(stmt.name)
        self.ctx.local_scope_names.add(stmt.name)
        self.ctx.var_types[stmt.name] = target_type
        if self.ctx.current_ns and target_type:
            self.ctx.current_ns.bind_variable(stmt.name, target_type)
        if (stmt.init is not None
                and isinstance(target_type, TupleType)
                and target_type.has_pointer_repr_optional_element()
                and self.ctx.is_storage_form_source(stmt.init)):
            self.ctx.storage_form_tuple_locals.add(stmt.name)

        cpp_type = self._resolve_cpp_type(stmt)

        # @dynamic protocol types always use adapter slots + Base* pointer-local
        if self._is_dynamic_protocol_type(target_type):
            assert stmt.init, f"@dynamic protocol local '{stmt.name}' requires initializer"
            self.ctx.pointer_locals.add(stmt.name)
            return self._gen_dynamic_protocol_init(stmt.name, target_type, stmt.init, indent)

        # TypeParamRef variable initialized from a user method or free function call: use
        # ::tpy::val_or_ref_t<T> (or ::tpy::val_or_cref_t<T> for readonly methods). This expands
        # to T for value types and T& (or const T&) for non-value types, matching the C++
        # return type semantics. Only for non-reassigned, non-hoisted vars -- rebinding a
        # val_or_ref_t alias is not possible in C++ (references can't be rebound), so
        # reassigned vars fall through to the rvalue/value-copy path instead.
        if (isinstance(target_type, TypeParamRef) and not target_type.is_value_type()
                and stmt.init is not None
                and isinstance(stmt.init, (TpyMethodCall, TpyCall))
                and stmt.name not in self.ctx.reassigned_vars
                and stmt.name not in self.ctx.hoisted_vars
                and stmt.name not in self.ctx.move_through_vars):
            fi = stmt.init.resolved_function_info
            if fi is not None and fi.cpp_template is None and isinstance(unwrap_ref_type(fi.return_type), TypeParamRef):
                init_expr = self.expressions.gen_expr(stmt.init, target_type)
                trait = "::tpy::val_or_cref_t" if fi.is_readonly else "::tpy::val_or_ref_t"
                return f"{indent}{trait}<{cpp_type}> {cpp_name} = {init_expr};\n"

        # Pointer-variant locals for non-value unions
        if self.ctx.is_ptr_variant_union(target_type):
            return self._gen_ptr_variant_local_init(stmt, target_type, cpp_name, indent)

        # Indirection for non-value types in function/method scope
        if self._needs_indirection(target_type, stmt.name, stmt.init):
            is_optional = isinstance(target_type, OptionalType)
            if not is_optional:
                assert stmt.init, f"indirect local '{stmt.name}' missing initializer"
            is_const = self._is_const_indirect(target_type, stmt.init, stmt)
            if is_const:
                self.ctx.const_indirect_locals.add(stmt.name)
            const_pfx = "const " if is_const else ""
            if is_optional or stmt.name in self.ctx.reassigned_vars or stmt.name in self.ctx.hoisted_vars:
                # T* pointer-local -- needs rebinding support (or hoisted storage)
                self.ctx.pointer_locals.add(stmt.name)
                if stmt.name in self.ctx.sema_movable_locals:
                    self.ctx.movable_locals.add(stmt.name)
                if stmt.init:
                    return self._gen_pointer_local_init(stmt.name, cpp_type, stmt.init, target_type, indent)
                else:
                    # Optional without initializer -> nullptr
                    return f"{indent}{const_pfx}{cpp_type}* {cpp_name} = nullptr;\n"
            else:
                # T& reference -- alias without rebinding
                self.ctx.ref_bound_locals.add(stmt.name)
                init_expr = self.expressions.gen_expr_deref(stmt.init, target_type)
                init_inner = stmt.init.expr if isinstance(stmt.init, TpyCoerce) else stmt.init
                # 8a.5: for element borrow locals, determine const from the source.
                # Check whether the container param (or a const-ref local) is const T&
                # so the element borrow gets the matching explicit type.
                if (not is_const
                        and isinstance(init_inner, TpySubscript)
                        and not isinstance(init_inner.index, TpySlice)):
                    src_obj = init_inner.obj
                    src_is_const = (
                        isinstance(src_obj, TpyName)
                        and (src_obj.name in self.ctx.const_ref_params
                             or src_obj.name in self.ctx.const_indirect_locals)
                    )
                    if src_is_const:
                        # Propagate: downstream element borrows of this local are also const.
                        self.ctx.const_indirect_locals.add(stmt.name)
                        return f"{indent}const {cpp_type}& {cpp_name} = {init_expr};\n"
                # Alias/field borrow of a const ref: propagate const so the alias
                # also binds as const T& (required when source is const T&).
                if not is_const and isinstance(init_inner, TpyName):
                    src = init_inner.name
                    if (src in self.ctx.const_ref_params
                            or src in self.ctx.const_indirect_locals):
                        self.ctx.const_indirect_locals.add(stmt.name)
                        return f"{indent}const {cpp_type}& {cpp_name} = {init_expr};\n"
                return f"{indent}{const_pfx}{cpp_type}& {cpp_name} = {init_expr};\n"

        # Tier 1 non-value-type locals are eligible for auto-move at last use
        if (target_type and not target_type.is_value_type()
                and stmt.name in self.ctx.sema_movable_locals):
            self.ctx.movable_locals.add(stmt.name)

        if stmt.init:
            init_expr = self.expressions.gen_expr(stmt.init, target_type)
            if stmt.name in self.ctx.move_through_vars:
                init_expr = f"std::move({init_expr})"
            # Unwrap value-optional init when target is non-Optional
            if (
                not isinstance(target_type, OptionalType)
                and self._is_value_optional_expr(stmt.init)
            ):
                analyzed_type = self.ctx.get_expr_type(stmt.init)
                if isinstance(analyzed_type, OptionalType):
                    init_expr = f"::tpy::deref_optional_check({init_expr})"
                else:
                    init_expr = f"(*{init_expr})"
            # string_view -> string init requires explicit conversion in C++.
            elif is_str_type(target_type):
                if self._is_str_view_source(stmt.init):
                    init_expr = f"std::string({init_expr})"
            # span -> vector init requires explicit conversion in C++.
            elif is_bytes_type(target_type):
                if self._is_bytes_view_source(stmt.init):
                    init_expr = f"::tpy::bytes_copy({init_expr})"
            return f"{indent}{cpp_type} {cpp_name} = {init_expr};\n"
        else:
            return f"{indent}{cpp_type} {cpp_name};\n"

    def _gen_assign_code(self, stmt: TpyAssign, indent: str) -> str:
        """Generate code for an assignment. Returns code to write."""
        # Clear stale assignment narrowing on reassignment
        if isinstance(stmt.target, TpyName):
            self.ctx.assign_narrowed_types.pop(stmt.target.name, None)
            self.ctx.literal_facts.pop(stmt.target.name, None)
        # Slice assignment: a[x:y] = rhs -> list_set_slice
        if isinstance(stmt.target, TpySubscript) and isinstance(stmt.target.index, TpySlice):
            return self._gen_slice_assign(stmt, indent)
        # TypedDict subscript assignment: d["key"] = val -> d.key = val
        if isinstance(stmt.target, TpySubscript) and stmt.target.typed_dict_field is not None:
            obj = self.expressions.gen_expr(stmt.target.obj)
            subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(stmt.target.obj) else obj
            target_type = self.ctx.get_expr_type(stmt.target)
            value = self.expressions.gen_expr(stmt.value, target_type)
            value = self.expressions._maybe_move(stmt.value, value)
            cpp_field = escape_cpp_name(stmt.target.typed_dict_field)
            return f"{indent}{subscript_obj}.{cpp_field} = {value};\n"
        # Special handling for subscript assignment
        if isinstance(stmt.target, TpySubscript):
            obj = self.expressions.gen_expr(stmt.target.obj)
            is_indirect = self.ctx.is_indirect_name(stmt.target.obj)
            # Unwrap a sema-narrowed value-Optional receiver before passing
            # it to __setitem__; mirrors the subscript-read path.
            obj = self.expressions._maybe_unwrap_narrowed_optional(
                stmt.target.obj, obj, is_indirect)
            target_type = self.ctx.get_expr_type(stmt.target)
            value = self.expressions.gen_expr(stmt.value, target_type)
            value = self.expressions._maybe_move(stmt.value, value)
            value = self._maybe_wrap_tuple_to_storage(
                value, target_type, self.ctx.unwrap_copy(stmt.value))
            obj_type = self.ctx.get_expr_type(stmt.target.obj)
            index_type = self.ctx.analyzer.get_expr_type(stmt.target.index)
            # Dereference globals for subscript access
            subscript_obj = f"(*{obj})" if is_indirect else obj
            # Thread the dict/set's view-typed key through so bytes/str
            # literals pin to static storage (avoiding a stored dangling view).
            index_expr = self.expressions.gen_index_expr(
                stmt.target.index, index_type, view_key_target(obj_type))

            # Bounds-safe: index provably in [0, len(obj)), skip normalize_index.
            # See the matching note in expressions.py _gen_subscript: cast to
            # size_t for non-literal indices to avoid -Wsign-conversion,
            # except for concrete user records whose operator[] takes the
            # user's declared param type (typically int32_t).
            if stmt.target.bounds_safe:
                if (isinstance(stmt.target.index, TpyIntLiteral)
                        or _is_concrete_user_record(obj_type,
                                                     self.ctx.analyzer.registry)):
                    return f"{indent}{subscript_obj}[{index_expr}] = {value};\n"
                return f"{indent}{subscript_obj}[static_cast<std::size_t>({index_expr})] = {value};\n"

            # Use registry lookup for __setitem__
            fi = self.builtins.get_type_method_fi(obj_type, "__setitem__")
            if fi:
                code = self.builtins.gen_call_from_fi(fi, subscript_obj, [index_expr, value])
                return f"{indent}{code};\n"
            else:
                return f"{indent}::tpy::__setitem__({subscript_obj}, {index_expr}, {value});\n"

        # Pointer-local rebinding (e.g., x.field = ... where x is pointer-local handled by field access)
        if isinstance(stmt.target, TpyName) and stmt.target.name in self.ctx.pointer_locals:
            target_type = self.ctx.var_types.get(stmt.target.name)
            cpp_type = self.types.type_to_cpp(target_type) if target_type else "auto"
            return self._gen_pointer_local_rebind(stmt.target.name, cpp_type, stmt.value, target_type, indent)

        # Property setter: delegate to normal method call codegen
        if isinstance(stmt.target, TpyFieldAccess) and stmt.target.property_setter_call is not None:
            call = self.expressions._gen_method_call(stmt.target.property_setter_call)
            return f"{indent}{call};\n"

        # D16 dyn-attr __setattr__ fallback: delegate to normal method call codegen
        if isinstance(stmt.target, TpyFieldAccess) and stmt.target.dyn_setattr_call is not None:
            call = self.expressions._gen_method_call(stmt.target.dyn_setattr_call)
            return f"{indent}{call};\n"

        # Field-target type lookup: use the declared field type, not sema's
        # cached expression type. After narrowing (e.g. `if self.x is None:
        # return`), the cached type for the field-access LHS is the narrowed
        # inner, which would route None-assign past the OptionalType branch
        # and emit bare `nullptr` against `std::optional<...>`. Reused below
        # by the field-shape dispatch, the class-constant branch, and the
        # default fallthrough -- all three are field-target paths.
        field_target_type: TpyType | None = None
        if isinstance(stmt.target, TpyFieldAccess):
            declared = self.expressions._get_cpp_declared_type(stmt.target)
            field_target_type = (declared if declared is not None
                                 else self.ctx.get_expr_type(stmt.target))

        # Field assignment: boundary conversions for optional/union pointer repr.
        if isinstance(stmt.target, TpyFieldAccess):
            target_type = field_target_type
            if (isinstance(target_type, TupleType)
                    and target_type.has_pointer_repr_optional_element()):
                source = self.ctx.unwrap_copy(stmt.value)
                target = self.expressions.gen_expr(stmt.target)
                value = self.expressions.gen_expr(stmt.value, target_type)
                value = self._maybe_wrap_tuple_to_storage(value, target_type, source)
                return f"{indent}{target} = {value};\n"
            # Optional field: std::optional<T> storage needs boundary conversion
            if isinstance(target_type, OptionalType) and target_type.uses_pointer_repr():
                target = self.expressions.gen_expr(stmt.target)
                # OPTIONAL_STORAGE source (Own[Opt[T_ref]] param): already
                # optional<T>, direct std::move into the field.
                if (isinstance(stmt.value, TpyName)
                        and self.ctx.needs_optional_to_ptr_lift(stmt.value.name)):
                    value = self.expressions.gen_expr(stmt.value)
                    value = self.expressions._maybe_move(stmt.value, value)
                    return f"{indent}{target} = {value};\n"
                # Value source is T* (pointer-local, function returning Optional) -> wrap
                if self.ctx.is_indirect_name(stmt.value):
                    value = self.expressions.gen_expr(stmt.value)
                    return f"{indent}{target} = ::tpy::ptr_to_optional({value});\n"
                raw_val_type = self.ctx.get_expr_type(stmt.value)
                val_type = raw_val_type.wrapped if isinstance(raw_val_type, OwnType) else raw_val_type
                source = self.ctx.unwrap_copy(stmt.value)
                if (isinstance(val_type, OptionalType)
                        and not isinstance(source, TpyFieldAccess)
                        and not self.ctx.is_storage_form_optional_source(source)):
                    # Own[T] | None returns std::optional<T> -- direct assign
                    # T | None returns T* -- needs ptr_to_optional wrapping
                    is_owned_optional = (isinstance(val_type, OptionalType)
                                         and isinstance(val_type.inner, OwnType))
                    # Own[Optional[T]] param is std::optional<T>&& -- also direct assign
                    if not is_owned_optional and isinstance(stmt.value, TpyName):
                        param_type = self.ctx.current_func_params.get(stmt.value.name)
                        if isinstance(param_type, OwnType):
                            is_owned_optional = True
                    value = self.expressions.gen_expr(stmt.value, target_type)
                    if is_owned_optional:
                        value = self.expressions._maybe_move(stmt.value, value)
                        return f"{indent}{target} = {value};\n"
                    return f"{indent}{target} = ::tpy::ptr_to_optional({value});\n"
                # Direct value or optional-to-optional (field-to-field) works without conversion
                value = self.expressions.gen_expr_deref(stmt.value, target_type)
                value = self.expressions._maybe_move(stmt.value, value)
                return f"{indent}{target} = {value};\n"
            # Union field: pointer-variant source -> value-variant field conversion
            if self.ctx.is_ptr_variant_union(target_type):
                target = self.expressions.gen_expr(stmt.target)
                value = self.expressions.gen_expr(stmt.value, target_type)
                if self.ctx.is_ptr_variant_source(stmt.value):
                    val_cpp = self.types.type_to_cpp(target_type)
                    value = f"::tpy::to_value_variant<{val_cpp}>({value})"
                else:
                    value = self.expressions._maybe_move(stmt.value, value)
                return f"{indent}{target} = {value};\n"
            # Ptr[T] field: storage-form Optional source needs optional_to_ptr
            # lift to mirror the storage-form-to-borrow-form bridge already
            # done for OptionalType destinations above.
            if isinstance(target_type, PtrType):
                source = self.ctx.unwrap_copy(stmt.value)
                raw_val_type = self.ctx.get_expr_type(stmt.value)
                val_type = raw_val_type.wrapped if isinstance(raw_val_type, OwnType) else raw_val_type
                if (isinstance(val_type, OptionalType)
                        and val_type.uses_pointer_repr()
                        and self.ctx.is_storage_form_optional_source(source)):
                    target = self.expressions.gen_expr(stmt.target)
                    value = self.expressions.gen_expr(stmt.value, target_type)
                    return f"{indent}{target} = ::tpy::optional_to_ptr({value});\n"

        # Class-constant write: emit any receiver-side effects as a leading
        # statement so the qualified `<owner>::<member>` appears as a real
        # lvalue. Going through `gen_expr` would wrap it in a GCC statement
        # expression (an rvalue), which fails to compile on the LHS of `=`.
        if (isinstance(stmt.target, TpyFieldAccess)
                and stmt.target.class_constant_owner is not None):
            receiver_stmt, lvalue = self.expressions.gen_class_constant_lvalue(stmt.target)
            target_type = field_target_type
            value = self.expressions.gen_expr_deref(stmt.value, target_type)
            value = self.expressions._maybe_move(stmt.value, value)
            prefix = f"{indent}{receiver_stmt};\n" if receiver_stmt else ""
            return f"{prefix}{indent}{lvalue} = {value};\n"

        # Default: simple assignment (includes field assignments like self.x = val).
        target = self.expressions.gen_expr(stmt.target)
        target_type = (field_target_type if isinstance(stmt.target, TpyFieldAccess)
                       else self.ctx.get_expr_type(stmt.target))
        # Detect x = x + y on string types -> emit x += y for buffer reuse
        if isinstance(stmt.target, TpyName):
            if result := self._try_str_inplace_append(stmt.target.name, target, stmt.value, target_type, indent):
                return result
        value = self.expressions.gen_expr_deref(stmt.value, target_type)
        value = self.expressions._maybe_move(stmt.value, value)
        return f"{indent}{target} = {value};\n"

    def _gen_del_item_code(self, stmt: TpyDelItem, indent: str) -> str:
        """Generate code for del obj[key] statement."""
        parts: list[str] = []
        for subscript in stmt.targets:
            obj = self.expressions.gen_expr(subscript.obj)
            obj_type = self.ctx.get_expr_type(subscript.obj)
            index_type = self.ctx.analyzer.get_expr_type(subscript.index)
            subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(subscript.obj) else obj
            index_expr = self.expressions.gen_index_expr(subscript.index, index_type)

            fi = self.builtins.get_type_method_fi(obj_type, "__delitem__")
            if fi:
                code = self.builtins.gen_call_from_fi(fi, subscript_obj, [index_expr])
                parts.append(f"{indent}{code};\n")
            else:
                parts.append(f"{indent}::tpy::__delitem__({subscript_obj}, {index_expr});\n")
        return "".join(parts)

    def _gen_del_attr_code(self, stmt: TpyDelAttr, indent: str) -> str:
        """D16 Phase 3: codegen for `del obj.foo` -- delegate to __delattr__."""
        parts: list[str] = []
        for target in stmt.targets:
            assert target.dyn_delattr_call is not None, (
                "TpyDelAttr without resolved dyn_delattr_call: sema bug")
            call = self.expressions._gen_method_call(target.dyn_delattr_call)
            parts.append(f"{indent}{call};\n")
        return "".join(parts)

    def _gen_del_var_code(self, stmt: TpyDelVar, indent: str) -> str:
        """Generate code for variable deletion (del x).

        Moves the value into a temporary that is immediately destroyed,
        releasing resources early. Move-sink is only emitted when the
        variable is the sole owner of its value:
        - Skip trivially destructible types (no-op)
        - Skip T& aliases (source still owns it)
        - Skip sources of T& aliases (alias still references it)
        - Skip pointer-locals that started as aliases (may point at source's storage)
        - Skip parameters (non-value params are const T& -- can't move from const)
        - Skip globals (other code may access it)
        Pointer-locals that own their value use std::move(*name) to deref first.
        """
        parts: list[str] = []
        for name in stmt.names:
            var_type = self.ctx.var_types.get(name)
            if var_type and var_type.is_trivially_destructible():
                continue
            if name in self.ctx.ref_bound_locals:
                continue
            if name in self.ctx.aliased_vars:
                continue
            if name in self.ctx.current_func_params:
                continue
            if name in self.ctx.global_declared_vars:
                continue
            if name in self.ctx.pointer_locals:
                if name in self.ctx.alias_names:
                    continue
                cpp_name = escape_cpp_name(name)
                parts.append(f"{indent}{{ auto __del_sink = std::move(*{cpp_name}); }}\n")
                continue
            cpp_name = escape_cpp_name(name)
            parts.append(f"{indent}{{ auto __del_sink = std::move({cpp_name}); }}\n")
        return "".join(parts)

    def _gen_aug_assign_code(self, stmt: TpyAugAssign, indent: str) -> str:
        """Generate code for an augmented assignment. Returns code to write."""
        if isinstance(stmt.target, TpyName):
            self.ctx.literal_facts.pop(stmt.target.name, None)
        # Special handling for subscript targets - use set_value() pattern
        # TypedDict subscript generates as field access, so the general path handles it
        if isinstance(stmt.target, TpySubscript) and stmt.target.typed_dict_field is None:
            return self._gen_aug_assign_subscript_code(stmt, indent)

        # In-place operator (__iadd__, __ior__, etc.) -- mutates target directly
        if inplace := stmt.resolved_inplace:
            target = self.expressions.gen_expr(stmt.target)
            if self.ctx.is_indirect_name(stmt.target):
                target = f"(*{target})"
            value = self.expressions.gen_expr_deref(stmt.value)
            # C++ can't deduce template params from bare initializer lists when
            # the function uses a two-parameter template (e.g. list_extend(T&, Container)).
            # Prefix with explicit vector type so the range overload resolves cleanly.
            receiver_type = self.ctx.get_expr_type(stmt.target)
            if isinstance(stmt.value, TpyArrayLiteral) and is_list(receiver_type):
                value = f"{self.types.type_to_cpp(receiver_type)}{value}"
            result = self.builtins.gen_call_from_fi(inplace.method, target, [value])
            return f"{indent}{result};\n"

        # Class-constant aug-assign: split receiver eval off so the qualified
        # name is a real lvalue and is emitted at most once. Going through
        # `gen_expr` for the target would wrap it in a GCC statement expression
        # (rvalue), and substituting that into `target = bin_op(target, v)`
        # would also evaluate the receiver twice.
        receiver_stmt = ""
        if (isinstance(stmt.target, TpyFieldAccess)
                and stmt.target.class_constant_owner is not None):
            receiver_stmt, target = self.expressions.gen_class_constant_lvalue(stmt.target)
        else:
            target = self.expressions.gen_expr(stmt.target)
            # Unwrap a sema-narrowed value-Optional LHS so the synthesized
            # `target = target op value` reads the inner T, not std::optional<T>.
            target = self.expressions._maybe_unwrap_narrowed_optional(
                stmt.target, target, self.ctx.is_indirect_name(stmt.target))
        target_type = self.ctx.get_expr_type(stmt.target)
        value = self.expressions.gen_expr(stmt.value, target_type)
        value_type = self.types.get_resolved_type(stmt.value, target_type)

        # Special case: FixedInt += BigInt should convert BigInt to the target type
        # This preserves checked arithmetic and avoids unnecessary promotion to BigInt
        if is_fixed_int_type(target_type) and is_big_int_type(value_type):
            # Dereference globals before .to_fixed_check<T>() conversion
            if self.ctx.is_indirect_name(stmt.value):
                value = f"(*{value})"
            value = f"({value}).to_fixed_check<{target_type.to_cpp()}>()"
            value_type = target_type

        prefix = f"{indent}{receiver_stmt};\n" if receiver_stmt else ""
        # Use resolved binop from sema for augmented assignment (a += b is a = a + b)
        if binop_result := stmt.resolved_binop:
            # String += optimization: in-place append instead of allocating a new string
            if (is_str_type(target_type) or is_string_type(target_type)
                    or isinstance(target_type, PendingStrType)) and stmt.op == "+":
                return f"{prefix}{indent}{target} += {value};\n"
            result = self.expressions._gen_binop_from_result(binop_result, target, value)
            return f"{prefix}{indent}{target} = {result};\n"
        else:
            # Fallback for operators not in module system
            cpp_op = "/" if stmt.op == "//" else stmt.op
            return f"{prefix}{indent}{target} {cpp_op}= {value};\n"

    def _gen_aug_assign_subscript_code(self, stmt: TpyAugAssign, indent: str) -> str:
        """Generate code for augmented assignment to subscript targets.

        Uses set_value(container, index, get_value(container, index) op value) pattern
        for range-checked read and write. Only supported for value type elements.
        """
        if not isinstance(stmt.target, TpySubscript):
            raise CodeGenError("Expected subscript target for augmented assignment", stmt.loc)
        subscript = stmt.target
        obj = self.expressions.gen_expr(subscript.obj)
        is_indirect = self.ctx.is_indirect_name(subscript.obj)
        # Unwrap a sema-narrowed value-Optional receiver so __getitem__ /
        # __setitem__ see the inner container; mirrors _gen_assign_code.
        obj = self.expressions._maybe_unwrap_narrowed_optional(
            subscript.obj, obj, is_indirect)
        obj_type = self.types.get_resolved_type(subscript.obj)
        index_type = self.ctx.get_expr_type(subscript.index)
        # Dereference globals for subscript access
        subscript_obj = f"(*{obj})" if is_indirect else obj
        index_expr = self.expressions.gen_index_expr(subscript.index, index_type or INT32)

        # Get element type
        elem_type = obj_type.get_element_type()

        # Only allow augmented assignment on value type elements
        if elem_type and not elem_type.is_value_type():
            raise RuntimeError(
                f"Augmented assignment on container elements not supported for object types "
                f"(element type: {elem_type})"
            )

        # Generate read expression using registry lookup for __getitem__
        get_fi = self.builtins.get_type_method_fi(obj_type, "__getitem__")
        if get_fi:
            read_expr = self.builtins.gen_call_from_fi(get_fi, subscript_obj, [index_expr])
        else:
            read_expr = f"{subscript_obj}[{index_expr}]"

        value = self.expressions.gen_expr(stmt.value, elem_type)
        value_type = self.types.get_resolved_type(stmt.value, elem_type)

        # Special case: FixedInt += BigInt should convert BigInt to the element type
        if is_fixed_int_type(elem_type) and is_big_int_type(value_type):
            # Dereference globals before .to_fixed_check<T>() conversion
            if self.ctx.is_indirect_name(stmt.value):
                value = f"(*{value})"
            value = f"({value}).to_fixed_check<{elem_type.to_cpp()}>()"
            value_type = elem_type

        # Compute the result expression using resolved binop from sema
        if binop_result := stmt.resolved_binop:
            result_expr = self.expressions._gen_binop_from_result(binop_result, read_expr, value)
        else:
            cpp_op = "/" if stmt.op == "//" else stmt.op
            result_expr = f"{read_expr} {cpp_op} {value}"

        # Generate write using registry lookup for __setitem__
        set_fi = self.builtins.get_type_method_fi(obj_type, "__setitem__")
        if set_fi:
            code = self.builtins.gen_call_from_fi(set_fi, subscript_obj, [index_expr, result_expr])
            return f"{indent}{code};\n"
        else:
            return f"{indent}::tpy::__setitem__({subscript_obj}, {index_expr}, {result_expr});\n"

    def _try_str_inplace_append(
        self, target_name: str, target_cpp: str, value_expr: TpyExpr,
        target_type: TpyType, indent: str,
    ) -> str | None:
        """Emit x += rhs if value_expr is x + rhs on a string type, else None."""
        if not (is_str_type(target_type) or is_string_type(target_type)
                or isinstance(target_type, PendingStrType)):
            return None
        inner = value_expr
        while isinstance(inner, TpyCoerce):
            inner = inner.expr
        if (isinstance(inner, TpyBinOp) and inner.op == "+"
                and isinstance(inner.left, TpyName)
                and inner.left.name == target_name):
            rhs = self.expressions.gen_expr_deref(inner.right, target_type)
            return f"{indent}{target_cpp} += {rhs};\n"
        return None

    def _is_optional_str_param(self, expr: TpyExpr) -> bool:
        """Check if expr is an Optional[str] function parameter (string_view in C++)."""
        if not isinstance(expr, TpyName):
            return False
        declared = self.ctx.current_func_params.get(expr.name)
        return (isinstance(declared, OptionalType)
                and is_str_type(declared.inner))

    def _is_str_view_source(self, expr: TpyExpr) -> bool:
        """Check if expr produces std::string_view at C++ runtime and needs explicit std::string().

        Bare string literals generate const char* and can implicitly construct std::string,
        so they don't need an explicit wrapper and return False.

        For all other expressions, two checks are combined:
        1. If sema/local_deduction annotated the expression as StrView (e.g. an or-chain
           where a promoted local forced the annotation), trust that annotation.
        2. Otherwise delegate to expressions._is_str_view_at_runtime, which uses AND
           semantics and handles str params (typed as str in sema but string_view in C++).
        """
        if isinstance(expr, TpyStrLiteral):
            return False
        if is_str_view_type(self.types.get_resolved_type(expr)):
            return True
        # Note: TpyCoerce is not explicitly handled here. If a coercion wrapping
        # a string_view source (e.g. narrowed Optional[str] param) ever appears
        # as a var init with a str target, the coerce target type resolves to
        # str (not StrView), and _is_str_view_at_runtime returns False,
        # so no std::string() wrap would be emitted. That path is currently not
        # reachable in practice (Optional[str] coercions go through the
        # _is_optional_str_param / _expr_uses_optional_str_param guards instead).
        return self.expressions._is_str_view_at_runtime(expr)

    def _is_bytes_view_source(self, expr: TpyExpr) -> bool:
        """Check if expr produces std::span<const uint8_t> at C++ runtime.

        Bytes literals are temporary vectors (not view-safe), so return False.
        Bytes params are spans, so return True.
        """
        if is_bytes_view_type(self.types.get_resolved_type(expr)):
            return True
        return self.expressions._is_bytes_view_at_runtime(expr)

    def _expr_uses_optional_str_param(self, expr: TpyExpr) -> bool:
        """Check if expr (e.g. ternary) dereferences an Optional[str] param."""
        if self._is_optional_str_param(expr):
            return True
        if isinstance(expr, TpyIfExpr):
            return (self._expr_uses_optional_str_param(expr.then_expr)
                    or self._expr_uses_optional_str_param(expr.else_expr))
        return False

    def _is_value_optional_var(self, name: str) -> bool:
        """Check if a variable's C++ declared type is a value-type std::optional<T>."""
        declared = self.ctx.var_types.get(name) or self.ctx.current_func_params.get(name)
        return (isinstance(declared, OptionalType) and not declared.uses_pointer_repr())

    def _is_value_optional_expr(self, expr: TpyExpr) -> bool:
        """Check if an expression's C++ type is a value-type std::optional<T>.

        Handles both TpyName (variable) and TpyFieldAccess (obj.field).
        """
        if isinstance(expr, TpyName):
            return self._is_value_optional_var(expr.name)
        declared = self.expressions._get_cpp_declared_type(expr)
        return (isinstance(declared, OptionalType) and not declared.uses_pointer_repr())


    def _emit_isinstance_extractions(
        self, out: TextIO, type_facts: dict[str, TpyType], indent_extra: int = 1,
    ) -> dict[str, str | None]:
        """Emit std::get extractions for isinstance-narrowed variables.

        Returns saved narrowed_vars entries for later restoration.
        Only emits extraction when the fact is a concrete (non-union) type.
        indent_extra controls how many indent levels past the current level to emit at:
        1 (default) for inside an if-block, 0 for after an assert at the current level.
        """
        saved: dict[str, str | None] = {}
        if not type_facts:
            return saved
        inner_indent = INDENT * (self.ctx.indent_level + indent_extra)
        for var_name, narrowed_type in type_facts.items():
            if isinstance(narrowed_type, (UnionType, NoneType)):
                continue
            # LiteralType narrowing: track for dead branch elimination,
            # no std::get extraction needed.
            if isinstance(narrowed_type, LiteralType):
                self.ctx.literal_facts[var_name] = narrowed_type
                continue
            # Protocol isinstance narrows the concept constraint, not the value;
            # no std::get extraction needed (the variable is already a T& ref).
            # Track the narrowed type so get_resolved_type surfaces it to
            # downstream dispatch (for-loop peephole, `in` operator, etc.).
            if is_protocol_type(narrowed_type):
                self.ctx.protocol_narrowings[var_name] = narrowed_type
                continue
            # In @overload context, the param is already the concrete type --
            # no std::get extraction needed.
            if var_name in self.ctx.overload_param_types:
                continue
            cpp_type = self.types.type_to_cpp(narrowed_type)
            # Any narrowing (D15): the source variable is a tpy::Any cell;
            # the narrowed binding is a `const T&` borrow into its
            # contents. The outer Any survives unchanged.
            var_decl = self.ctx.lookup_var_type(var_name)
            if isinstance(var_decl, AnyType):
                local_name = f"__{var_name}"
                if self.ctx.is_indirect_name(TpyName(var_name)):
                    var_ref = f"(*{var_name})"
                else:
                    var_ref = var_name
                out.write(
                    f"{inner_indent}const {cpp_type}& {local_name} = "
                    f"std::any_cast<const {cpp_type}&>({var_ref}.value);\n"
                )
                saved[var_name] = self.ctx.narrowed_vars.get(var_name)
                self.ctx.narrowed_vars[var_name] = local_name
                continue
            # std::get needs the underlying variant. Previously-extracted T&
            # aliases in narrowed_vars (from outer if-branch narrowing, match
            # binds, or inline isinstance facts) point at non-variants, so we
            # must target the original variable here.
            if self.ctx.is_indirect_name(TpyName(var_name)):
                var_ref = f"(*{var_name})"
            else:
                var_ref = var_name
            local_name = f"__{var_name}"
            # Value-type union params are const&, so std::get yields const T&.
            # Non-value union params and locals are mutable.
            var_decl_type = self.ctx.var_types.get(var_name)
            is_const = (var_name in self.ctx.current_func_params
                        and var_decl_type is not None
                        and (var_decl_type.is_value_type() or self.ctx.is_recursive_union(var_decl_type)))
            qualifier = "const auto&" if is_const else "auto&"
            # Pointer-variant unions: *std::get<T*>(var) or *std::get<const T*>(var)
            if var_name in self.ctx.ptr_variant_locals:
                const_pfx = "const " if var_name in self.ctx.const_indirect_locals else ""
                out.write(f"{inner_indent}{qualifier} {local_name} = *std::get<{const_pfx}{cpp_type}*>({var_ref});\n")
            else:
                # Recursive union wrapper: access .data for variant operations
                get_ref = self.ctx.variant_data_expr(var_ref, var_decl_type) if var_decl_type else var_ref
                out.write(f"{inner_indent}{qualifier} {local_name} = std::get<{cpp_type}>({get_ref});\n")
            saved[var_name] = self.ctx.narrowed_vars.get(var_name)
            self.ctx.narrowed_vars[var_name] = local_name
        return saved


    def _unpack_source_has_const_slots(self, stmt: TpyTupleUnpack) -> bool:
        """True when the unpack source has const-typed borrow slots.

        Triggered when the source is a name referring to either:
        - a const-inferred param (deep_const_borrow_params), or
        - a synthesized for-loop tuple iterating a const-bound source
          (const_storage_form_tuple_locals).
        Drives the unpack codegen to emit `const T*` / `const T&` for
        unpacked locals rather than `T*` / `T&` (which would fail to bind
        from the const slot).
        """
        if not isinstance(stmt.value, TpyName):
            return False
        return (stmt.value.name in self.ctx.deep_const_borrow_params
                or stmt.value.name in self.ctx.const_storage_form_tuple_locals)

    def _gen_tuple_unpack(self, out: TextIO, stmt: TpyTupleUnpack, indent: str) -> None:
        """Generate tuple unpacking: auto __tup_N = expr; T a = std::get<0>(...); ..."""
        # Inside try/except: intercept error_return calls with goto dispatch
        if self.ctx.try_except_label and self._get_error_return_fi(stmt.value):
            self.ctx.try_except_counter += 1
            try_tmp = f"__try_tmp_{self.ctx.try_except_counter}"
            label = self.ctx.try_except_label
            call_cpp = self._gen_error_return_call(stmt.value)
            self.ctx.temps.flush(out, indent)
            out.write(f"{indent}{{\n")
            out.write(f"{indent}{INDENT}auto {try_tmp} = {call_cpp};\n")
            out.write(self._gen_error_goto(f"{indent}{INDENT}", try_tmp, label))
            out.write(f"{indent}}}\n")
            # Use unwrapped value for the rest of tuple unpacking
            unwrapped_tmp = f"(::tpy::unwrap_ref(*{try_tmp}))"
        else:
            unwrapped_tmp = None

        wrapped_to_pointer = False
        if unwrapped_tmp:
            value_expr = unwrapped_tmp
        else:
            value_expr = self.expressions.gen_expr(stmt.value)
            self.ctx.temps.flush(out, indent)
            wrapped = self._maybe_wrap_storage_tuple_source(stmt, value_expr)
            wrapped_to_pointer = wrapped is not value_expr
            value_expr = wrapped

        self.ctx.unpack_counter += 1
        tmp = f"__tup_{self.ctx.unpack_counter}"
        # When value is a named variable and no elements need move semantics,
        # bind by ref to avoid copying the tuple.  Sema guarantees
        # is_ref[i] implies not is_owned[i], so if any element needs a
        # move we take the copy path instead.  Within the ref path, use
        # const only when no element needs a mutable reference (is_ref);
        # std::get on a const tuple returns const T& which can't bind
        # to T&.  Existing is_const_ref elements are unaffected -- const T&
        # binds fine from a non-const tuple.
        # tuple_to_pointer wrap returns a prvalue: bind by value, not by ref.
        if (not unwrapped_tmp and not wrapped_to_pointer
                and isinstance(stmt.value, TpyName) and not any(stmt.is_owned)):
            const_kw = "" if any(stmt.is_ref) else "const "
            out.write(f"{indent}{const_kw}auto& {tmp} = {value_expr};\n")
        else:
            out.write(f"{indent}auto {tmp} = {value_expr};\n")

        source_has_const_slots = self._unpack_source_has_const_slots(stmt)
        for i, name in enumerate(stmt.targets):
            if name is None:
                continue
            target_type = stmt.target_types[i]
            # Ref in target type means reference binding -- unwrap for C++ type
            # since the binding mode (ref/const_ref/value) is handled below.
            if isinstance(target_type, RefType):
                target_type = target_type.wrapped
            cpp_type = self.types.type_to_cpp(target_type)
            cpp_name = escape_cpp_name(name)
            get_expr = f"std::get<{i}>({tmp})"
            # Pointer-repr Optional element: slot is T*, register the local
            # in pointer_locals so subsequent reads know to deref.
            is_ptr_optional = (isinstance(target_type, OptionalType)
                               and target_type.uses_pointer_repr())
            if is_ptr_optional and stmt.is_new[i]:
                self.ctx.declared_vars.add(name)
                self.ctx.local_scope_names.add(name)
                self.ctx.var_types[name] = target_type
                self.ctx.pointer_locals.add(name)
                is_const = source_has_const_slots or (
                    stmt.is_const_ref
                    and i < len(stmt.is_const_ref)
                    and stmt.is_const_ref[i])
                if is_const:
                    self.ctx.const_indirect_locals.add(name)
                ptr_cpp = (target_type.to_cpp_return_const() if is_const
                           else target_type.to_cpp_return())
                out.write(f"{indent}{ptr_cpp} {cpp_name} = {get_expr};\n")
                continue
            # Pointer-variant Union element: declare as variant<T*,...> via
            # to_ptr_variant lift. Without this the local is value-variant
            # (variant<A, B>) and subsequent uses that expect pointer-variant
            # (call args, returns) fail to convert. Mirror of the pointer-repr
            # Optional branch above.
            is_ptr_variant = self.ctx.is_ptr_variant_union(target_type)
            if is_ptr_variant and stmt.is_new[i]:
                self.ctx.declared_vars.add(name)
                self.ctx.local_scope_names.add(name)
                self.ctx.var_types[name] = target_type
                self.ctx.ptr_variant_locals.add(name)
                pv_cpp = self.types.type_to_cpp_ptr_variant(target_type)
                out.write(
                    f"{indent}{pv_cpp} {cpp_name} = "
                    f"::tpy::to_ptr_variant({get_expr});\n"
                )
                continue
            if stmt.is_ref[i]:
                # Unwrap val_or_ref<T> from iterator-composed tuples
                # (e.g. enumerate(map(f, xs)) yields tuple<int, val_or_ref<T>>).
                # No-op for plain T& elements from regular tuples.
                get_expr = f"::tpy::unwrap_ref({get_expr})"
            if stmt.is_owned[i]:
                get_expr = f"std::move({get_expr})"

            if stmt.is_new[i]:
                # Variable pre-declared for loop hoisting -- emit assignment,
                # not re-declaration.
                already_declared = name in self.ctx.loop_hoisted_vars
                self.ctx.declared_vars.add(name)
                self.ctx.local_scope_names.add(name)
                self.ctx.var_types[name] = target_type
                if already_declared:
                    out.write(f"{indent}{cpp_name} = {get_expr};\n")
                elif stmt.is_ref[i]:
                    if name in self.ctx.reassigned_vars or name in self.ctx.hoisted_vars:
                        self.ctx.pointer_locals.add(name)
                        cv = "const " if source_has_const_slots else ""
                        if source_has_const_slots:
                            self.ctx.const_indirect_locals.add(name)
                        out.write(f"{indent}{cv}{cpp_type}* {cpp_name} = "
                                  f"&{get_expr};\n")
                    else:
                        cv = "const " if source_has_const_slots else ""
                        out.write(f"{indent}{cv}{cpp_type}& {cpp_name} = "
                                  f"{get_expr};\n")
                elif (stmt.is_const_ref and i < len(stmt.is_const_ref)
                        and stmt.is_const_ref[i]):
                    out.write(f"{indent}const {cpp_type}& {cpp_name} = "
                              f"{get_expr};\n")
                else:
                    out.write(f"{indent}{cpp_type} {cpp_name} = "
                              f"{get_expr};\n")
            else:
                if name in self.ctx.pointer_locals:
                    rebind_slot = self.ctx.rebind_slots.get(name)
                    if rebind_slot:
                        is_optional_slot = rebind_slot not in self.ctx.plain_rebind_slots
                        deref = self._ptr_from_rvalue_slot(
                            rebind_slot, get_expr, False, is_optional_slot)
                        out.write(f"{indent}{cpp_name} = {deref};\n")
                    else:
                        slot = self.ctx.slots.next_slot()
                        self.ctx.rebind_slots[name] = slot
                        is_hoisted = name in self.ctx.hoisted_vars or name in self.ctx.branch_hoisted_vars
                        if is_hoisted:
                            hoist_kw = "static " if self.ctx.slots.global_scope else ""
                            slot_opt = f"std::optional<{cpp_type}>"
                            self.ctx.pending_hoist_decls.append(
                                f"{hoist_kw}{slot_opt} {slot};\n")
                            deref = self._ptr_from_rvalue_slot(
                                slot, get_expr, False, True)
                            out.write(f"{indent}{cpp_name} = {deref};\n")
                        else:
                            self.ctx.plain_rebind_slots.add(slot)
                            static_kw = "static " if self.ctx.slots.global_scope else ""
                            out.write(f"{indent}{static_kw}{cpp_type} {slot} = "
                                      f"{get_expr};\n")
                            out.write(f"{indent}{cpp_name} = &{slot};\n")
                else:
                    out.write(f"{indent}{cpp_name} = "
                              f"{get_expr};\n")

    def _gen_with(self, out: TextIO, stmt: 'TpyWith', indent: str) -> None:
        """Generate a with statement using the unified try-with-finally shape.

        Emits per context manager:
            auto __ctx_N = <context_expr>;
            auto& x = __ctx_N.__enter__();
            try {
                <body>
                __ctx_N.__exit__();      // normal fall-through
            } catch (...) {
                __ctx_N.__exit__();
                throw;
            }

        When the as-variable name is reused across multiple with blocks
        (detected by prescan as reassigned), the variable is emitted as a
        T* pointer-local so it can be rebound without C++ redeclaration.

        The ctx and as-variable are hoisted outside the try scope so they
        remain visible after the with block (matching CPython semantics).
        Multiple context managers nest -- innermost __exit__() runs first.
        """
        self.ctx.temps.flush(out, indent)

        ctx_ids: list[int] = []
        for item in stmt.items:
            self.ctx.with_counter += 1
            n = self.ctx.with_counter
            ctx_ids.append(n)

            ctx_expr = self.expressions.gen_expr(item.context_expr)
            out.write(f"{indent}auto __ctx_{n} = {ctx_expr};\n")

            if item.target is not None:
                assert item.enter_type is not None
                name = item.target
                is_reassigned = name in self.ctx.reassigned_vars
                already_declared = name in self.ctx.declared_vars

                if already_declared:
                    if name in self.ctx.optional_locals:
                        out.write(f"{indent}{name} = __ctx_{n}.__enter__();\n")
                    else:
                        out.write(f"{indent}{name} = &(__ctx_{n}.__enter__());\n")
                elif is_reassigned and not item.enter_type.is_value_type():
                    cpp_type = self.types.type_to_cpp(item.enter_type)
                    out.write(f"{indent}{cpp_type}* {name} = &(__ctx_{n}.__enter__());\n")
                    self.ctx.pointer_locals.add(name)
                else:
                    if item.enter_type.is_value_type():
                        out.write(f"{indent}auto {name} = __ctx_{n}.__enter__();\n")
                    else:
                        out.write(f"{indent}auto& {name} = __ctx_{n}.__enter__();\n")

                if not already_declared:
                    self.ctx.declared_vars.add(name)
                    self.ctx.local_scope_names.add(name)
                    self.ctx.var_types[name] = item.enter_type
            else:
                out.write(f"{indent}__ctx_{n}.__enter__();\n")

        # Nest try-catch blocks outermost-to-innermost; LIFO close so
        # innermost __exit__ runs first.
        body_terminates = stmts_terminate(stmt.body)

        def emit_innermost_body(o: TextIO, body_indent: str) -> None:
            for s in stmt.body:
                self.gen_stmt(o, s)

        emit_body: Callable[[TextIO, str], None] = emit_innermost_body
        layer_terminates = body_terminates
        for ctx_n, item in zip(reversed(ctx_ids), reversed(stmt.items)):
            inner_emit = emit_body
            can_suppress = item.exit_can_suppress
            takes_exc_val = item.exit_takes_exc_val

            def make_layer(inner_emit_fn, ctx_n_val, can_suppress_val,
                           takes_exc_val_val, layer_terminates_val):
                def layer(o: TextIO, body_indent: str) -> None:
                    self._emit_with_try_catch(
                        o, body_indent, inner_emit_fn,
                        ctx_n=ctx_n_val,
                        can_suppress=can_suppress_val,
                        takes_exc_val=takes_exc_val_val,
                        body_terminates=layer_terminates_val)
                return layer
            emit_body = make_layer(inner_emit, ctx_n, can_suppress,
                                   takes_exc_val, layer_terminates)
            # Once an inner layer may suppress, the outer layer's body (the
            # inner try/catch) can fall through even when the Python body
            # always raises -- so propagate False to outer layers.
            if can_suppress:
                layer_terminates = False

        emit_body(out, indent)

    def _gen_nested_def(self, out: TextIO, stmt: TpyNestedDef, indent: str) -> None:
        """Generate a C++ lambda for a nested function definition."""

        func = stmt.func
        name = escape_cpp_name(func.name)

        # Build capture list
        if stmt.captured_names:
            if stmt.escapes:
                # Mixed capture: ref for non-value outer params,
                # move for last-use locals, value (copy) for the rest
                parts = []
                for n in stmt.captured_names:
                    cpp_n = escape_cpp_name(n)
                    if n in stmt.ref_captures:
                        parts.append(f"&{cpp_n}")
                    elif n in stmt.move_captures:
                        parts.append(f"{cpp_n} = std::move({cpp_n})")
                    else:
                        parts.append(cpp_n)
                capture = f"[{', '.join(parts)}]"
            else:
                refs = ", ".join(f"&{escape_cpp_name(n)}" for n in stmt.captured_names)
                capture = f"[{refs}]"
        else:
            capture = "[]"

        # Build parameter list
        params = []
        for pname, ptype in func.params:
            resolved = self.types.resolve_type(ptype)
            cpp_name = escape_cpp_name(pname)
            cpp_type = resolved.to_cpp_param(cpp_name)
            params.append(cpp_type)
        params_str = ", ".join(params)

        # Return type
        return_type = self.types.resolve_type(func.return_type)
        if isinstance(return_type, VoidType):
            ret_annotation = ""
        else:
            ret_cpp = self.types.type_to_cpp(return_type)
            ret_annotation = f" -> {ret_cpp}"

        # Save outer codegen scope so lambda body declarations don't leak
        scope_snap = self.ctx.snapshot_local_scope()
        for pname, _ in func.params:
            self.ctx.local_scope_names.add(pname)
        self.ctx.local_scope_names.add(func.name)
        self.ctx.nested_def_locals.add(func.name)

        # Emit lambda header
        out.write(f"{indent}auto {name} = {capture}({params_str}){ret_annotation} {{\n")

        # Increase indent and generate body
        self.ctx.indent_level += 1
        try:
            for s in func.body:
                self.gen_stmt(out, s)
        finally:
            self.ctx.indent_level -= 1
            self.ctx.restore_local_scope(scope_snap)
            # Re-add nested def name (must survive into outer scope)
            self.ctx.nested_def_locals.add(func.name)
            self.ctx.local_scope_names.add(func.name)

        out.write(f"{indent}}};\n")

    def _gen_raise(self, stmt: TpyRaise, indent: str) -> str:
        """Generate a raise statement (return-tier, throw-tier, or bare re-raise)."""
        # Bare raise (re-raise)
        if stmt.exception_type is None and stmt.raise_expr is None:
            if self.ctx.in_except_tier == "return":
                assert self.ctx.try_except_err_opt is not None
                expr = (f"::tpy::make_unexpected("
                        f"std::move(*{self.ctx.try_except_err_opt}))")
                return self._make_return(indent, expr)
            else:
                # Throw-tier re-raise
                return f"{indent}throw;\n"

        # Expression raise (throw-tier only)
        if stmt.raise_expr is not None:
            expr = self.expressions.gen_expr_deref(stmt.raise_expr)
            return f"{indent}throw {expr};\n"

        cpp_type = error_return_to_cpp(stmt.exception_type, self.ctx.analyzer.ctx.module_name, self.ctx.analyzer.registry)
        is_cf = is_return_exception(stmt.exception_type)

        if is_cf:
            # Return-tier: return std::unexpected
            if stmt.args:
                args = ", ".join(self.expressions.gen_expr(a) for a in stmt.args)
                return self._make_return(indent, f"::tpy::make_unexpected({cpp_type}({args}))")
            return self._make_return(indent, f"::tpy::make_unexpected({cpp_type}{{}})")
        else:
            # Throw-tier: C++ throw
            if stmt.args:
                args = ", ".join(self.expressions.gen_expr(a) for a in stmt.args)
                return f"{indent}throw {cpp_type}({args});\n"
            return f"{indent}throw {cpp_type}{{}};\n"

    def _gen_try(self, out: TextIO, stmt: TpyTry, indent: str) -> None:
        """Generate a try/except/else/finally statement."""
        tier = stmt.tier
        if tier == "finally_only":
            self._gen_try_finally_only(out, stmt, indent)
        elif tier == "return":
            self._gen_try_return(out, stmt, indent)
        else:
            self._gen_try_throw(out, stmt, indent)

    def _push_finally(self, emit_finally: Callable[[TextIO, str], None],
                      terminates: bool) -> FinallyContext:
        """Push a finally frame onto the active stack.

        loop_depth captures len(loop_else_labels) at push time so
        break/continue can identify finally frames inside the innermost
        active loop body.
        """
        fctx = FinallyContext(
            emit_finally=emit_finally,
            terminates=terminates,
            loop_depth=len(self.ctx.loop_else_labels),
        )
        self.ctx.finally_stack.append(fctx)
        return fctx

    def _emit_except_handler_header(self, out: TextIO, handler) -> None:
        """Emit a single ` catch (...) {` clause header for `handler`.
        Caller is responsible for emitting the handler body and the
        closing `}`. Shared by sync try/except codegen and the
        async-await try/except path in `gen_async.py`.
        """
        if handler.exception_type is None:
            out.write(" catch (...) {\n")
            return
        cpp_type = error_return_to_cpp(
            handler.exception_type,
            self.ctx.analyzer.ctx.module_name,
            self.ctx.analyzer.registry)
        if handler.binding:
            binding = escape_cpp_name(handler.binding)
            out.write(f" catch (const {cpp_type}& {binding}) {{\n")
        else:
            out.write(f" catch (const {cpp_type}&) {{\n")

    def _emit_finally_chain(self, out: TextIO, indent: str,
                            stop_at: int = 0) -> bool:
        """Emit finally bodies inline from innermost down to stop_at (exclusive).

        Each finally body is emitted with the corresponding frame popped, so
        any return/break/continue inside it redirects through the outer
        frames -- not back through itself. The stack is restored on exit so
        subsequent code in the caller's scope is unaffected (relevant when
        emitting the normal-fall-through finally before popping in the
        caller).

        ``indent_level`` is temporarily synced to the ``indent`` string so
        that gen_stmt-based emit_finally callbacks (which read
        self.ctx.indent_level rather than the ``ind`` argument) emit at the
        correct depth. Callers may pass an indent that doesn't correspond
        to the current emission point (e.g. _gen_propagate_check emits a
        nested return inside an `if` body); the level is restored after.

        Returns True if any finally body terminates (raise/return) -- the
        caller must suppress its own trailing return/break/continue/throw
        in that case, since control already left.
        """
        snapshot = list(self.ctx.finally_stack)
        prev_indent_level = self.ctx.indent_level
        target_level = len(indent) // len(INDENT)
        terminated = False
        try:
            self.ctx.indent_level = target_level
            while len(self.ctx.finally_stack) > stop_at:
                fctx = self.ctx.finally_stack.pop()
                fctx.emit_finally(out, indent)
                if fctx.terminates:
                    terminated = True
                    break
        finally:
            self.ctx.indent_level = prev_indent_level
            self.ctx.finally_stack = snapshot
        return terminated

    def _make_try_finally_emit(self, stmt: TpyTry) -> tuple[Callable[[TextIO, str], None], bool]:
        """Build an emit callback and terminates flag for a try/finally's body."""
        def emit(o: TextIO, ind: str) -> None:
            for s in stmt.finally_body:
                self.gen_stmt(o, s)
        last = stmt.finally_body[-1] if stmt.finally_body else None
        terminates = isinstance(last, (TpyRaise, TpyReturn))
        return emit, terminates

    def _make_return(self, indent: str, expr: str | None = None) -> str:
        """Generate a return statement, walking finally chain inline first.

        Each enclosing try/with's finally body is emitted as inline C++ code
        before the actual `return ...;`. If a finally body itself terminates
        (via raise/return), the trailing return is suppressed (the body
        already transferred control).
        """
        out = io.StringIO()
        terminated = self._emit_finally_chain(out, indent)
        if terminated:
            return out.getvalue()
        if expr is None:
            out.write(f"{indent}return;\n")
        else:
            out.write(f"{indent}return {expr};\n")
        return out.getvalue()

    def _make_async_return(self, stmt: TpyReturn, indent: str) -> str:
        """Lower `return v` inside an `async def` body to:

            <walk active finally frames>
            __state = S_DONE;
            return ::tpy::Poll<T>::ready(<v>);

        For void-returning async defs:
            return ::tpy::Poll<std::monostate>::ready(std::monostate{});

        For bare `return`:
            void -> Poll<std::monostate>::ready(std::monostate{})
            non-void -> sema rejects elsewhere; here we panic on `{}` to be safe.
        """
        ret_type = unwrap_ref_type(self.ctx.current_return_type)
        done_state = self.ctx.async_coro_done_state or "S_DONE"
        out = io.StringIO()
        # Walk enclosing finally chain (try/with around an `await` or just a
        # return inside try/finally). Same machinery as sync _make_return.
        terminated = self._emit_finally_chain(out, indent)
        if terminated:
            return out.getvalue()
        out.write(f"{indent}__state = {done_state};\n")
        if isinstance(ret_type, VoidType):
            out.write(f"{indent}{POLL_VOID_READY_RETURN}\n")
        else:
            if stmt.value is None:
                # Non-void async def with bare return -- sema should have
                # caught this; emit a panic as a guardrail.
                out.write(
                    f"{indent}::tpy::tpy_panic(\"non-void async def used bare return\");\n")
            else:
                ret_cpp = self.ctx.async_coro_return_cpp or "void"
                expr_cpp = self.expressions.gen_expr_deref(stmt.value)
                out.write(
                    f"{indent}return ::tpy::Poll<{ret_cpp}>::ready({expr_cpp});\n")
        return out.getvalue()

    def _make_break_continue(self, indent: str, *, is_break: bool) -> str:
        """Generate break/continue, walking finally chain inline first.

        Only finally frames pushed inside the innermost active loop body run
        before the break/continue -- frames around the loop itself stay on
        the stack and run when their try-with-finally exits normally later.
        """
        out = io.StringIO()
        loop_count = len(self.ctx.loop_else_labels)
        # Stack is monotone non-decreasing in loop_depth (deeper-nested
        # frames push later). Find the first index whose loop_depth >=
        # loop_count: those frames sit inside the innermost loop body.
        boundary = len(self.ctx.finally_stack)
        for i, fctx in enumerate(self.ctx.finally_stack):
            if fctx.loop_depth >= loop_count:
                boundary = i
                break
        terminated = self._emit_finally_chain(out, indent, stop_at=boundary)
        if terminated:
            return out.getvalue()
        if is_break:
            else_label = (self.ctx.loop_else_labels[-1]
                          if self.ctx.loop_else_labels else None)
            if else_label:
                out.write(f"{indent}goto {else_label};\n")
            else:
                out.write(f"{indent}break;\n")
        else:
            out.write(f"{indent}continue;\n")
        return out.getvalue()

    def _emit_with_try_catch(
            self,
            out: TextIO,
            inner: str,
            emit_body: Callable[[TextIO, str], None],
            ctx_n: int,
            can_suppress: bool,
            takes_exc_val: bool,
            body_terminates: bool,
    ) -> None:
        """Emit the `with`-specific try/catch shape (v1.5 M1).

        Layout (full, when can_suppress or takes_exc_val):
            try {
                <body>
                __ctx_N.__exit__({}, nullptr, {});  // normal fall-through
            } catch (::tpy::BaseException& __exc_N) {
                // can_suppress=True (return type bool):
                if (!__ctx_N.__exit__({}, &__exc_N, {})) throw;
                // can_suppress=False (return type None):
                __ctx_N.__exit__({}, &__exc_N, {});
                throw;
            } catch (...) {
                // Foreign (non-tpy) exception -- best-effort cleanup;
                // no suppression possible because exc_val typed
                // Optional[BaseException] can't carry a foreign value.
                __ctx_N.__exit__({}, nullptr, {});
                throw;
            }

        When !can_suppress and !takes_exc_val, the BaseException& catch
        and the foreign catch would emit byte-identical bodies (both:
        `__exit__({}, {}, {}); throw;`). Elide the BaseException catch
        in that case -- cleanup-only managers (the common stdlib shape)
        emit one catch instead of two.

        Normal-path __exit__ is INSIDE the try (last stmt after the
        body) so a suppressing catch doesn't double-call it on
        fall-through. Push a finally frame for return/break/continue
        through the body (matches `_emit_try_with_finally`'s contract).
        """
        exc_null_arg = "nullptr" if takes_exc_val else "{}"
        exc_obj_arg = f"&__exc_{ctx_n}" if takes_exc_val else "{}"
        emit_tpy_catch = can_suppress or takes_exc_val

        def emit_normal_exit(o: TextIO, ind: str) -> None:
            o.write(f"{ind}__ctx_{ctx_n}.__exit__({{}}, {exc_null_arg}, {{}});\n")

        self._push_finally(emit_normal_exit, terminates=False)

        out.write(f"{inner}try {{\n")
        self.ctx.indent_level += 1
        emit_body(out, self.ctx.indent())
        if not body_terminates:
            emit_normal_exit(out, self.ctx.indent())
        self.ctx.indent_level -= 1

        # Pop the frame before emitting catches so a nested raise/return
        # inside __exit__'s body walks outer frames, not back through
        # itself.
        if emit_tpy_catch:
            out.write(f"{inner}}} catch (::tpy::BaseException& __exc_{ctx_n}) {{\n")
            self.ctx.indent_level += 1
            self.ctx.finally_stack.pop()
            catch_ind = self.ctx.indent()
            if can_suppress:
                out.write(
                    f"{catch_ind}if (!__ctx_{ctx_n}.__exit__({{}}, "
                    f"{exc_obj_arg}, {{}})) throw;\n")
            else:
                out.write(
                    f"{catch_ind}__ctx_{ctx_n}.__exit__({{}}, "
                    f"{exc_obj_arg}, {{}});\n")
                out.write(f"{catch_ind}throw;\n")
            self.ctx.indent_level -= 1
            out.write(f"{inner}}} catch (...) {{\n")
        else:
            out.write(f"{inner}}} catch (...) {{\n")
            self.ctx.finally_stack.pop()
        self.ctx.indent_level += 1
        catch_ind = self.ctx.indent()
        emit_normal_exit(out, catch_ind)
        out.write(f"{catch_ind}throw;\n")
        self.ctx.indent_level -= 1
        out.write(f"{inner}}}\n")

    def _emit_try_with_finally(
            self,
            out: TextIO,
            inner: str,
            emit_body: Callable[[TextIO, str], None],
            emit_finally: Callable[[TextIO, str], None],
            finally_terminates: bool,
            body_terminates: bool,
    ) -> None:
        """Emit the unified `try { body } catch (...) { F; throw; } F;` shape.

        ``emit_body`` is invoked with the finally frame on the stack so
        return/break/continue inside the body walk it via _emit_finally_chain.
        ``emit_finally`` is called twice -- once on the catch path with the
        frame popped, once on the normal-fall-through path. When the finally
        body itself terminates, the trailing `throw;` after the catch path
        is suppressed; when the try body unconditionally terminates on every
        path, the normal-path emission is also skipped (no fall-through to
        worry about).
        """
        fctx = self._push_finally(emit_finally, finally_terminates)

        out.write(f"{inner}try {{\n")
        self.ctx.indent_level += 1
        emit_body(out, self.ctx.indent())
        self.ctx.indent_level -= 1
        out.write(f"{inner}}} catch (...) {{\n")
        self.ctx.indent_level += 1
        # Pop the frame so a raise/return inside the finally body redirects
        # through the OUTER frames, not back through itself. The same pop
        # also affects the normal-path emission below.
        self.ctx.finally_stack.pop()
        catch_indent = self.ctx.indent()
        emit_finally(out, catch_indent)
        if not finally_terminates:
            out.write(f"{catch_indent}throw;\n")
        self.ctx.indent_level -= 1
        out.write(f"{inner}}}\n")

        # Normal-path finally: emit only if the body might fall through.
        if not body_terminates:
            emit_finally(out, inner)

    def _gen_try_finally_only(self, out: TextIO, stmt: TpyTry, indent: str) -> None:
        """Generate try/finally with no except handlers.

        Unified shape (no goto, no __retval):
            {
                try {
                    <try body>
                } catch (...) {
                    <finally body>
                    throw;
                }
                <finally body>   // normal fall-through, omitted if body terminates
            }

        return/break/continue inside the try body emit their own inline
        finally call before the actual exit (see _make_return /
        _make_break_continue). The catch-path finally runs with the frame
        popped so internal raise/return redirect to outer frames.
        """
        out.write(f"{indent}{{\n")
        self.ctx.indent_level += 1
        inner = self.ctx.indent()

        emit_finally, terminates = self._make_try_finally_emit(stmt)

        def emit_body(o: TextIO, body_indent: str) -> None:
            for s in stmt.try_body:
                self.gen_stmt(o, s)

        self._emit_try_with_finally(
            out, inner, emit_body, emit_finally,
            finally_terminates=terminates,
            body_terminates=stmts_terminate(stmt.try_body))

        self.ctx.indent_level -= 1
        out.write(f"{indent}}}\n")

    def _gen_try_return(self, out: TextIO, stmt: TpyTry, indent: str) -> None:
        """Generate return-tier try/except (goto-based error dispatch).

        When finally is present, the entire try/except block is wrapped in
        the unified try-with-finally so throw-tier exceptions that escape
        the inner code still trigger cleanup.
        """
        handler = stmt.handlers[0]
        self.ctx.try_except_counter += 1
        n = self.ctx.try_except_counter
        except_label = f"__except_{n}"
        after_label = f"__after_try_{n}"
        has_finally = bool(stmt.finally_body)

        out.write(f"{indent}{{\n")
        self.ctx.indent_level += 1
        inner = self.ctx.indent()

        # Emit std::optional<E> for except binding
        err_opt_var: str | None = None
        prev_err_opt = self.ctx.try_except_err_opt
        if handler.binding:
            err_opt_var = f"__err_opt_{n}"
            cpp_err_type = error_return_to_cpp(
                qualify_exception_name(handler.exception_type,
                                       self.ctx.analyzer.registry,
                                       self.ctx.analyzer.ctx.module_name),
                self.ctx.analyzer.ctx.module_name,
                self.ctx.analyzer.registry)
            out.write(f"{inner}std::optional<{cpp_err_type}> {err_opt_var};\n")
            self.ctx.try_except_err_opt = err_opt_var

        def emit_try_except(o: TextIO, body_indent: str) -> None:
            br_snap = self.ctx.snapshot_local_scope()
            prev_label = self.ctx.try_except_label
            self.ctx.try_except_label = except_label

            for s in stmt.try_body:
                self.gen_stmt(o, s)

            self.ctx.try_except_label = prev_label

            if stmt.else_body:
                o.write(f"{body_indent}// else:\n")
                for s in stmt.else_body:
                    self.gen_stmt(o, s)

            o.write(f"{body_indent}goto {after_label};\n")

            exc_display = handler.exception_type or "..."
            o.write(f"{body_indent}// except {exc_display}:\n")
            o.write(f"{body_indent}{except_label}:;\n")

            self.ctx.restore_local_scope(br_snap)

            prev_except_tier = self.ctx.in_except_tier
            self.ctx.in_except_tier = "return"
            if handler.binding and err_opt_var:
                binding = escape_cpp_name(handler.binding)
                o.write(f"{body_indent}{{\n")
                self.ctx.indent_level += 1
                inner2 = self.ctx.indent()
                o.write(f"{inner2}auto& {binding} = *{err_opt_var};\n")
                for s in handler.body:
                    self.gen_stmt(o, s)
                self.ctx.indent_level -= 1
                o.write(f"{body_indent}}}\n")
            else:
                for s in handler.body:
                    self.gen_stmt(o, s)
            self.ctx.in_except_tier = prev_except_tier

            o.write(f"{body_indent}{after_label}:;\n")

        if has_finally:
            emit_finally, terminates = self._make_try_finally_emit(stmt)
            self._emit_try_with_finally(
                out, inner, emit_try_except, emit_finally,
                finally_terminates=terminates,
                body_terminates=stmts_terminate([stmt]))
        else:
            emit_try_except(out, inner)

        self.ctx.try_except_err_opt = prev_err_opt
        self.ctx.indent_level -= 1
        out.write(f"{indent}}}\n")

    def _gen_try_throw(self, out: TextIO, stmt: TpyTry, indent: str) -> None:
        """Generate throw-tier try/except (C++ try/catch).

        When finally is present, the inner try/except is wrapped in the
        unified try-with-finally so re-raises and exceptions from handler
        bodies still trigger cleanup.
        """
        has_finally = bool(stmt.finally_body)

        out.write(f"{indent}{{\n")
        self.ctx.indent_level += 1
        inner = self.ctx.indent()

        has_else = bool(stmt.else_body)
        if has_else:
            self.ctx.try_except_counter += 1
            after_else_label = f"__after_else_{self.ctx.try_except_counter}"
        else:
            after_else_label = ""

        def emit_try_except(o: TextIO, body_indent: str) -> None:
            o.write(f"{body_indent}try {{\n")
            self.ctx.indent_level += 1
            for s in stmt.try_body:
                self.gen_stmt(o, s)
            self.ctx.indent_level -= 1
            o.write(f"{body_indent}}}")

            prev_except_tier = self.ctx.in_except_tier
            for h in stmt.handlers:
                self._emit_except_handler_header(o, h)
                self.ctx.indent_level += 1
                self.ctx.in_except_tier = "throw"
                for s in h.body:
                    self.gen_stmt(o, s)
                if has_else:
                    o.write(f"{self.ctx.indent()}goto {after_else_label};\n")
                self.ctx.in_except_tier = prev_except_tier
                self.ctx.indent_level -= 1
                o.write(f"{body_indent}}}")

            o.write("\n")

            if has_else:
                o.write(f"{body_indent}// else:\n")
                for s in stmt.else_body:
                    self.gen_stmt(o, s)
                o.write(f"{body_indent}{after_else_label}:;\n")

        if has_finally:
            emit_finally, terminates = self._make_try_finally_emit(stmt)
            self._emit_try_with_finally(
                out, inner, emit_try_except, emit_finally,
                finally_terminates=terminates,
                body_terminates=stmts_terminate([stmt]))
        else:
            emit_try_except(out, inner)

        self.ctx.indent_level -= 1
        out.write(f"{indent}}}\n")

    def _gen_error_goto(self, indent: str, tmp: str, label: str) -> str:
        """Generate the if-not-has_value goto, with optional error capture for 'as e'."""
        err_opt = self.ctx.try_except_err_opt
        if err_opt:
            return (f"{indent}if (!{tmp}.has_value()) "
                    f"{{ {err_opt} = std::move({tmp}.error()); goto {label}; }}\n")
        return f"{indent}if (!{tmp}.has_value()) goto {label};\n"

    def _gen_error_return_stmt_block(self, call_cpp: str, indent: str) -> str:
        """Wrap a fallible call as a statement block with the appropriate
        error-handling: goto-except (in try), propagate (in @error_return),
        or panic (top-level). Discards the success value."""
        self.ctx.try_except_counter += 1
        tmp = f"__try_tmp_{self.ctx.try_except_counter}"
        if self.ctx.try_except_label:
            check = self._gen_error_goto(f"{indent}{INDENT}", tmp, self.ctx.try_except_label)
        elif self.ctx.current_error_return:
            check = self._gen_propagate_check(f"{indent}{INDENT}", tmp)
        else:
            check = (f"{indent}{INDENT}if (!{tmp}.has_value()) "
                     f"::tpy::tpy_panic(\"unhandled error return\");\n")
        return (f"{indent}{{\n"
                f"{indent}{INDENT}auto {tmp} = {call_cpp};\n"
                f"{check}"
                f"{indent}}}\n")

    def _gen_propagate_check(self, indent: str, tmp: str) -> str:
        """Emit the propagate-out check for an @error_return call result.

        When inside a try-with-finally, the propagate-out path must run any
        active finally bodies before returning the unexpected value -- the
        unified _make_return walks the finally stack inline. The bare-return
        fast-path (no finally active) keeps the single-line shape."""
        if not self.ctx.finally_stack:
            return (f"{indent}if (!{tmp}.has_value()) "
                    f"return ::tpy::make_unexpected({tmp}.error());\n")
        body = self._make_return(
            indent + INDENT, f"::tpy::make_unexpected({tmp}.error())")
        return f"{indent}if (!{tmp}.has_value()) {{\n{body}{indent}}}\n"

    def _gen_error_return_var_decl(self, stmt: TpyVarDecl, indent: str) -> str:
        """Generate a variable declaration where the init is an @error_return call.

        Emits:
            auto __try_tmp_N = call();
            if (!__try_tmp_N.has_value()) goto __except_N;
            T var = *__try_tmp_N;
        """
        assert stmt.init is not None
        assert self.ctx.try_except_label is not None

        self.ctx.try_except_counter += 1
        tmp = f"__try_tmp_{self.ctx.try_except_counter}"
        label = self.ctx.try_except_label

        call_cpp = self._gen_error_return_call(stmt.init)
        cpp_name = escape_cpp_name(stmt.name)

        fi = self._get_error_return_fi(stmt.init)
        var_type = fi.return_type if fi else stmt.type

        # Declare variable before the goto to avoid "crosses initialization" error
        is_new_var = stmt.name not in self.ctx.declared_vars
        if is_new_var and var_type:
            cpp_type = unwrap_ref_type(var_type).to_cpp()
            out = f"{indent}{cpp_type} {cpp_name};\n"
            self.ctx.declared_vars.add(stmt.name)
        else:
            out = ""

        out += f"{indent}{{\n"
        out += f"{indent}{INDENT}auto {tmp} = {call_cpp};\n"
        out += self._gen_error_goto(f"{indent}{INDENT}", tmp, label)
        out += f"{indent}{INDENT}{cpp_name} = ::tpy::unwrap_ref(*{tmp});\n"
        out += f"{indent}}}\n"

        return out

    def _gen_error_return_assign(self, stmt: TpyAssign, indent: str) -> str:
        """Generate an assignment where the RHS is an @error_return call.

        Emits:
            auto __try_tmp_N = call();
            if (!__try_tmp_N.has_value()) goto __except_N;
            target = *__try_tmp_N;
        """
        assert self.ctx.try_except_label is not None

        self.ctx.try_except_counter += 1
        tmp = f"__try_tmp_{self.ctx.try_except_counter}"
        label = self.ctx.try_except_label

        call_cpp = self._gen_error_return_call(stmt.value)
        target_cpp = self.expressions.gen_expr(stmt.target)

        out = f"{indent}{{\n"
        out += f"{indent}{INDENT}auto {tmp} = {call_cpp};\n"
        out += self._gen_error_goto(f"{indent}{INDENT}", tmp, label)
        out += f"{indent}{INDENT}{target_cpp} = ::tpy::unwrap_ref(*{tmp});\n"
        out += f"{indent}}}\n"

        return out

    def _gen_error_return_propagate_var_decl(self, stmt: TpyVarDecl, indent: str) -> str:
        """Generate a variable declaration with auto-propagation.

        When an @error_return(E) function calls another @error_return(E) function
        outside a try/except, errors propagate automatically via early return.
        """
        assert stmt.init is not None
        assert self.ctx.current_error_return is not None

        self.ctx.try_except_counter += 1
        tmp = f"__try_tmp_{self.ctx.try_except_counter}"

        call_cpp = self._gen_error_return_call(stmt.init)
        cpp_name = escape_cpp_name(stmt.name)

        fi = self._get_error_return_fi(stmt.init)
        var_type = fi.return_type if fi else stmt.type

        is_new_var = stmt.name not in self.ctx.declared_vars
        if is_new_var and var_type:
            cpp_type = unwrap_ref_type(var_type).to_cpp()
            out = f"{indent}{cpp_type} {cpp_name};\n"
            self.ctx.declared_vars.add(stmt.name)
        else:
            out = ""

        out += f"{indent}{{\n"
        out += f"{indent}{INDENT}auto {tmp} = {call_cpp};\n"
        out += self._gen_propagate_check(f"{indent}{INDENT}", tmp)
        out += f"{indent}{INDENT}{cpp_name} = ::tpy::unwrap_ref(*{tmp});\n"
        out += f"{indent}}}\n"

        return out

    def _gen_error_return_propagate_assign(self, stmt: TpyAssign, indent: str) -> str:
        """Generate an assignment with auto-propagation."""
        assert self.ctx.current_error_return is not None

        self.ctx.try_except_counter += 1
        tmp = f"__try_tmp_{self.ctx.try_except_counter}"

        call_cpp = self._gen_error_return_call(stmt.value)
        target_cpp = self.expressions.gen_expr(stmt.target)

        out = f"{indent}{{\n"
        out += f"{indent}{INDENT}auto {tmp} = {call_cpp};\n"
        out += self._gen_propagate_check(f"{indent}{INDENT}", tmp)
        out += f"{indent}{INDENT}{target_cpp} = ::tpy::unwrap_ref(*{tmp});\n"
        out += f"{indent}}}\n"

        return out

    def _gen_error_return_unwrap_var_decl(self, stmt: TpyVarDecl, indent: str) -> str:
        """Generate a variable declaration with panic-on-error unwrap (top-level)."""
        assert stmt.init is not None

        self.ctx.try_except_counter += 1
        tmp = f"__try_tmp_{self.ctx.try_except_counter}"

        call_cpp = self._gen_error_return_call(stmt.init)
        cpp_name = escape_cpp_name(stmt.name)

        fi = self._get_error_return_fi(stmt.init)
        var_type = fi.return_type if fi else stmt.type

        is_new_var = stmt.name not in self.ctx.declared_vars
        if is_new_var and var_type:
            cpp_type = unwrap_ref_type(var_type).to_cpp()
            out = f"{indent}{cpp_type} {cpp_name};\n"
            self.ctx.declared_vars.add(stmt.name)
        else:
            out = ""

        out += f"{indent}{{\n"
        out += f"{indent}{INDENT}auto {tmp} = {call_cpp};\n"
        out += f"{indent}{INDENT}if (!{tmp}.has_value()) ::tpy::tpy_panic(\"unhandled error return\");\n"
        out += f"{indent}{INDENT}{cpp_name} = ::tpy::unwrap_ref(*{tmp});\n"
        out += f"{indent}}}\n"

        return out

    def _gen_error_return_unwrap_assign(self, stmt: TpyAssign, indent: str) -> str:
        """Generate an assignment with panic-on-error unwrap (top-level)."""
        self.ctx.try_except_counter += 1
        tmp = f"__try_tmp_{self.ctx.try_except_counter}"

        call_cpp = self._gen_error_return_call(stmt.value)
        target_cpp = self.expressions.gen_expr(stmt.target)

        out = f"{indent}{{\n"
        out += f"{indent}{INDENT}auto {tmp} = {call_cpp};\n"
        out += f"{indent}{INDENT}if (!{tmp}.has_value()) ::tpy::tpy_panic(\"unhandled error return\");\n"
        out += f"{indent}{INDENT}{target_cpp} = ::tpy::unwrap_ref(*{tmp});\n"
        out += f"{indent}}}\n"

        return out

    def _gen_error_return_call(self, expr: TpyExpr) -> str:
        """Generate an error_return call, suppressing expression-level unwrap.

        Statement-level handlers call this instead of gen_expr() directly
        so that the top-level call emits the raw std::expected (for the
        handler to unwrap), while nested error_return calls in arguments
        still get unwrapped via statement expressions.
        """
        self.ctx.error_return_stmt_handled = True
        return self.expressions.gen_expr(expr)

    def _get_error_return_fi(self, expr: TpyExpr) -> 'FunctionInfo | None':
        """Return FunctionInfo if expr is an @error_return call, else None."""
        if isinstance(expr, TpyCoerce):
            return self._get_error_return_fi(expr.expr)
        fi = None
        if isinstance(expr, (TpyCall, TpyMethodCall)):
            fi = getattr(expr, 'resolved_function_info', None)
        if fi and fi.error_return_type:
            return fi
        return None

    def _gen_yield(self, out: TextIO, stmt: TpyYield, indent: str) -> None:
        """Generate a yield statement in a generator function body."""
        state_num = self.ctx.analyzer.ctx.generator_yield_states[id(stmt)]
        yield_expr = self.gen_yield_value(stmt)
        out.write(f"{indent}__state = {state_num};\n")
        out.write(f"{indent}return {yield_expr};\n")
        out.write(f"{indent}__resume_{state_num}:;\n")

    def _gen_generator_for_loop(self, out: TextIO, stmt: TpyForEach, indent: str) -> None:
        """Generate a lowered for-loop inside a generator state machine body.

        For-loops with yields are lowered to while-loops so that goto resume
        labels can jump into the loop body (Duff's device pattern).
        """
        from .gen_generators import GeneratorForInfo
        info: GeneratorForInfo = self.ctx.generator_for_loop_info[id(stmt)]

        if info.strategy == "range":
            self._gen_generator_for_range(out, stmt, indent, info)
        elif info.strategy == "begin_end":
            self._gen_generator_for_begin_end(out, stmt, indent, info)
        elif info.strategy == "next":
            self._gen_generator_for_next(out, stmt, indent, info)
        elif info.strategy == "iter_next":
            self._gen_generator_for_iter_next(out, stmt, indent, info)
        else:
            raise CodeGenError(f"unknown generator for-loop strategy: {info.strategy}")

    def _gen_generator_for_range(self, out: TextIO, stmt: TpyForEach,
                                  indent: str, info: 'GeneratorForInfo') -> None:
        """Lowered range() for-loop: counter variables as struct fields."""
        from ..typesys import IntLiteralType

        uid = info.uid
        # Synthetic fields are std::optional -- dereference with *
        counter = f"*__for_i_{uid}"
        stop = f"*__for_stop_{uid}"
        counter_raw = f"__for_i_{uid}"
        stop_raw = f"__for_stop_{uid}"

        elem_type = stmt.elem_type
        if elem_type and isinstance(elem_type, IntLiteralType):
            elem_type = self.ctx.analyzer.ctx.default_int_type
        cpp_elem = self.types.type_to_cpp(elem_type) if elem_type else "int32_t"
        cpp_var = escape_cpp_name(stmt.var)

        range_call = stmt.iterable
        assert isinstance(range_call, TpyCall) and range_call.func_name == "range"
        gen_args = self.builtins.gen_range_args(range_call)
        nargs = len(gen_args)

        self.ctx.temps.flush(out, indent)

        # Initialize counter and stop (assign into optional)
        if nargs == 1:
            out.write(f"{indent}{counter_raw} = {cpp_elem}(0);\n")
            out.write(f"{indent}{stop_raw} = static_cast<{cpp_elem}>({gen_args[0]});\n")
        elif nargs >= 2:
            out.write(f"{indent}{counter_raw} = static_cast<{cpp_elem}>({gen_args[0]});\n")
            out.write(f"{indent}{stop_raw} = static_cast<{cpp_elem}>({gen_args[1]});\n")

        if nargs == 3:
            step_raw = f"__for_step_{uid}"
            step = f"*{step_raw}"
            step_lit = self._extract_int_literal(range_call.args[2])
            out.write(f"{indent}{step_raw} = static_cast<{cpp_elem}>({gen_args[2]});\n")
            if step_lit is None:
                out.write(f'{indent}::tpy::range_check_step_nonzero({step});\n')
            if is_big_int_type(elem_type):
                pass  # BigInt uses += directly
            else:
                self._gen_range_overflow_check(out, indent, counter, stop, step, elem_type)
            if step_lit is not None and step_lit > 0:
                cmp = "<"
            elif step_lit is not None and step_lit < 0:
                cmp = ">"
            else:
                cmp = None

            if cmp is not None:
                out.write(f"{indent}while ({counter} {cmp} {stop}) {{\n")
            else:
                out.write(f"{indent}while ({step} > 0 ? {counter} < {stop} "
                          f": {counter} > {stop}) {{\n")

            inner = indent + INDENT
            out.write(f"{inner}{cpp_var} = {counter};\n")
            out.write(f"{inner}{counter} += {step};\n")
        else:
            # step = 1 (default)
            out.write(f"{indent}while ({counter} < {stop}) {{\n")
            inner = indent + INDENT
            out.write(f"{inner}{cpp_var} = ({counter})++;\n")

        self._gen_generator_loop_body(out, stmt, indent)

    def _gen_generator_for_begin_end(self, out: TextIO, stmt: TpyForEach,
                                      indent: str, info: 'GeneratorForInfo') -> None:
        """Lowered NativeIterable for-loop: C++ begin/end iterators as struct fields."""
        uid = info.uid
        it_raw = f"__for_it_{uid}"
        end_raw = f"__for_end_{uid}"
        it = f"*{it_raw}"
        end = f"*{end_raw}"
        cpp_var = escape_cpp_name(stmt.var)

        elem_type = unwrap_ref_type(stmt.elem_type) if stmt.elem_type else None
        if elem_type:
            from ..typesys import IntLiteralType
            if isinstance(elem_type, IntLiteralType):
                elem_type = self.ctx.analyzer.ctx.default_int_type

        self.ctx.temps.flush(out, indent)

        # Determine the source expression
        has_src_field = any(fn.startswith(f"__for_src_{uid}") for fn, _ in info.fields)
        if has_src_field:
            src_raw = f"__for_src_{uid}"
            iterable_code = self.expressions.gen_expr(stmt.iterable)
            out.write(f"{indent}{src_raw} = {iterable_code};\n")
            src_name = f"*{src_raw}"
        else:
            src_name = self.expressions.gen_expr(stmt.iterable)

        out.write(f"{indent}{it_raw} = ({src_name}).begin();\n")
        out.write(f"{indent}{end_raw} = ({src_name}).end();\n")
        out.write(f"{indent}while ({it} != {end}) {{\n")

        inner = indent + INDENT
        out.write(f"{inner}{cpp_var} = *({it})++;\n")

        self._gen_generator_loop_body(out, stmt, indent)

    def _gen_generator_for_next(self, out: TextIO, stmt: TpyForEach,
                                 indent: str, info: 'GeneratorForInfo') -> None:
        """Lowered Iterator[T] for-loop: __next__() with std::expected struct field."""
        uid = info.uid
        r_raw = f"__for_r_{uid}"
        r = f"*{r_raw}"
        cpp_var = escape_cpp_name(stmt.var)

        self.ctx.temps.flush(out, indent)

        # Determine the iterator source (named field or stored expression)
        has_src_field = any(fn.startswith(f"__for_src_{uid}") for fn, _ in info.fields)
        if has_src_field:
            src_raw = f"__for_src_{uid}"
            iterable_code = self.expressions.gen_expr(stmt.iterable)
            out.write(f"{indent}{src_raw} = {iterable_code};\n")
            src_name = f"(*{src_raw})"
        else:
            src_name = self.expressions.gen_expr(stmt.iterable)

        out.write(f"{indent}for (;;) {{\n")

        inner = indent + INDENT
        out.write(f"{inner}{r_raw} = {src_name}.__next__();\n")
        out.write(f"{inner}if (!({r}).has_value()) break;\n")
        out.write(f"{inner}{cpp_var} = ::tpy::unwrap_ref(*({r}));\n")

        self._gen_generator_loop_body(out, stmt, indent)

    def _gen_generator_for_iter_next(self, out: TextIO, stmt: TpyForEach,
                                      indent: str, info: 'GeneratorForInfo') -> None:
        """Lowered ::tpy::__iter__() + __next__() for-loop (universal default)."""
        uid = info.uid
        itr_raw = f"__for_itr_{uid}"
        itr = f"*{itr_raw}"
        r_raw = f"__for_r_{uid}"
        r = f"*{r_raw}"
        cpp_var = escape_cpp_name(stmt.var)

        self.ctx.temps.flush(out, indent)

        iterable_code = self.expressions.gen_expr(stmt.iterable)
        out.write(f"{indent}{itr_raw} = ::tpy::__iter__({iterable_code});\n")

        out.write(f"{indent}for (;;) {{\n")
        inner = indent + INDENT
        out.write(f"{inner}{r_raw} = ({itr}).__next__();\n")
        out.write(f"{inner}if (!({r}).has_value()) break;\n")
        out.write(f"{inner}{cpp_var} = ::tpy::unwrap_ref(*({r}));\n")

        self._gen_generator_loop_body(out, stmt, indent)

    def _gen_generator_loop_body(self, out: TextIO, stmt: TpyForEach, indent: str) -> None:
        """Generate the body of a lowered generator for-loop and close the while/for."""
        # Register loop var as declared (it's a struct field, assigned above)
        self.ctx.declared_vars.add(stmt.var)
        self.ctx.local_scope_names.add(stmt.var)
        if stmt.elem_type:
            elem_type = unwrap_ref_type(stmt.elem_type)
            self.ctx.var_types[stmt.var] = elem_type
            # Generator bodies don't have a const-source channel today.
            self.ctx.register_loop_var_storage_form(
                stmt.var, elem_type, stmt.iterable, detect_const_source=False)

        old_ns = self.ctx.current_ns
        if self.ctx.current_ns and stmt.elem_type:
            inner_ns = Namespace(parent=self.ctx.current_ns)
            inner_ns.bind_variable(stmt.var, unwrap_ref_type(stmt.elem_type))
            self.ctx.current_ns = inner_ns

        body = stmt.body
        # Tuple unpack: first stmt is TpyTupleUnpack. Emit direct struct field
        # assignments instead of declarations (goto would jump over them).
        if stmt.is_tuple_unpack and body and isinstance(body[0], TpyTupleUnpack):
            unpack = body[0]
            inner = indent + INDENT
            # stmt.var is the synthetic __for_tup_N struct field assigned by the loop prologue
            loop_var = escape_cpp_name(stmt.var)
            for i, name in enumerate(unpack.targets):
                if name is None:
                    continue
                cpp_name = escape_cpp_name(name)
                self.ctx.declared_vars.add(name)
                self.ctx.local_scope_names.add(name)
                self.ctx.var_types[name] = unpack.target_types[i]
                out.write(f"{inner}{cpp_name} = std::get<{i}>({loop_var});\n")
            body = body[1:]

        self.ctx.indent_level += 1
        for s in body:
            self.gen_stmt(out, s)
        self.ctx.emit_block_trailing_comments(out, stmt.body, self.ctx.indent())
        self.ctx.indent_level -= 1

        self.ctx.current_ns = old_ns
        out.write(f"{indent}}}\n")

    def _gen_assert_throw(self, out: TextIO, stmt: TpyAssert, indent: str) -> str:
        """Generate the assertion-failure call for an assert statement."""
        if stmt.message is None:
            return '::tpy::raise_assertion_error()'
        if isinstance(stmt.message, TpyStrLiteral):
            msg = stmt.message.value.replace("\\", "\\\\").replace('"', '\\"')
            return f'::tpy::raise_assertion_error("{msg}")'
        msg_expr = self.expressions.gen_expr(stmt.message)
        self.ctx.temps.flush(out, indent)
        return f'::tpy::raise_assertion_error({msg_expr})'

    def _gen_assert(self, out: TextIO, stmt: TpyAssert, indent: str) -> None:
        """Generate an assert statement with optional isinstance union narrowing."""
        # Constant-fold trivially-known assertions (no temps to flush).
        if isinstance(stmt.condition, TpyBoolLiteral):
            if stmt.condition.value:
                return
            throw = self._gen_assert_throw(out, stmt, indent)
            out.write(f'{indent}{throw};\n')
            return
        if isinstance(stmt.condition, TpyNoneLiteral):
            throw = self._gen_assert_throw(out, stmt, indent)
            out.write(f'{indent}{throw};\n')
            return
        bool_cond = self.expressions.gen_truthy_expr(stmt.condition)
        self.ctx.temps.flush(out, indent)
        if stmt.message is None or isinstance(stmt.message, TpyStrLiteral):
            throw = self._gen_assert_throw(out, stmt, indent)
            out.write(f'{indent}if (!({bool_cond})) {throw};\n')
        else:
            # Evaluate message inside the if block (lazy, per Python semantics)
            inner = indent + "    "
            out.write(f'{indent}if (!({bool_cond})) {{\n')
            throw = self._gen_assert_throw(out, stmt, inner)
            out.write(f'{inner}{throw};\n')
            out.write(f'{indent}}}\n')
        # Emit std::get<T> extractions for isinstance-narrowed union variables.
        # Unlike if-branch narrowing, assert narrowing persists for the rest of scope,
        # so we do NOT call ctx.restore_narrowed_vars.
        self._emit_isinstance_extractions(out, stmt.then_type_facts, indent_extra=0)


    def _gen_if(self, out: TextIO, stmt: TpyIf, indent: str,
                emit_post_narrowing: bool = True,
                _skip_source_comment: bool = False) -> None:
        """Generate an if/elif/else chain as flat C++ if/else if/else."""
        # Collect the elif chain into a flat list of branches.
        # An elif is else_body == [TpyIf(...)] where the inner if has the
        # same column as the outer (genuinely nested else: if has deeper col).
        # We also only flatten when intermediate else_type_facts have no
        # concrete extractions (all union/none types).
        chain: list[TpyIf] = []
        current = stmt
        while True:
            chain.append(current)
            if (len(current.else_body) == 1
                    and isinstance(current.else_body[0], TpyIf)
                    and self._is_elif(current, current.else_body[0])
                    and not self._has_concrete_isinstance_facts(current.else_type_facts)):
                current = current.else_body[0]
            else:
                break

        # --- @overload dead branch elimination ---
        # When generating specialized overload code, isinstance checks on
        # parameters with known concrete types can be resolved statically.
        # Also applies to literal equality checks in literal specializations.
        if self.ctx.overload_param_types or self.ctx.literal_overload_facts:
            if self._gen_if_overload_specialized(out, chain, indent):
                return

        # Regular path: emit the `if` condition as a source comment.
        # _skip_source_comment is set by the elif-with-temps recursive path
        # which already emitted the comment before calling us.
        if not _skip_source_comment:
            self.ctx.emit_source_comment(out, stmt.loc, indent)

        # Pre-declare variables first declared inside branches (all levels).
        # Inner elif branch_decls are typically subsets of the outer's and
        # get skipped by the declared_vars check, but we emit them all for
        # correctness.
        for node in chain:
            self._emit_branch_decls(out, node, indent)

        # Snapshot after branch-decl hoisting so each branch starts with only
        # pre-hoisted vars visible (pointer-local slots created inside one branch
        # must not bleed into sibling branches).
        br_snap = self.ctx.snapshot_local_scope()

        # Emit if / else if / else chain
        for i, node in enumerate(chain):
            # Nullable protocol param narrowing: replace runtime `x != nullptr`
            # with compile-time `if constexpr (!std::same_as<T_x, nullptr_t>)`.
            # The pointer is guaranteed non-null for real types (call site passes &expr),
            # so the constexpr check alone is sufficient and avoids a redundant branch.
            constexpr_guards = self._get_nullproto_constexpr_guards(node.condition)

            if i > 0:
                self.ctx.emit_source_comment(out, node.loc, indent)

            if constexpr_guards:
                # Replace the runtime condition with if constexpr
                guard_conds = " && ".join(
                    f"!std::same_as<T_{gvar}, std::nullptr_t>" for gvar in constexpr_guards
                )
                if i == 0:
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}if constexpr ({guard_conds}) {{\n")
                elif not self.ctx.temps._pending and not self.ctx.temps._pending_named:
                    out.write(f"{indent}}} else if constexpr ({guard_conds}) {{\n")
                else:
                    self.ctx.temps._pending.clear()
                    out.write(f"{indent}}} else {{\n")
                    self.ctx.indent_level += 1
                    self.ctx.temps.flush(out, self.ctx.indent())
                    self._gen_if(out, node, self.ctx.indent(),
                                emit_post_narrowing=False,
                                _skip_source_comment=True)
                    self.ctx.indent_level -= 1
                    out.write(f"{indent}}}\n")
                    return
            else:
                cond = self.expressions.gen_truthy_expr(node.condition)
                is_constexpr = self._is_protocol_isinstance_condition(node.condition)
                if_kw = "if constexpr" if is_constexpr else "if"
                if i == 0:
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}{if_kw} ({cond}) {{\n")
                elif not self.ctx.temps._pending and not self.ctx.temps._pending_named:
                    else_kw = "else if constexpr" if is_constexpr else "else if"
                    out.write(f"{indent}}} {else_kw} ({cond}) {{\n")
                else:
                    # Elif condition produced temp/walrus vars -- can't use flat
                    # else-if. Regular temps are discarded and regenerated by
                    # recursive _gen_if. Walrus pre-decls are flushed inside
                    # the else block (walrus_pre_declared prevents re-creation).
                    self.ctx.temps._pending.clear()
                    out.write(f"{indent}}} else {{\n")
                    self.ctx.indent_level += 1
                    self.ctx.temps.flush(out, self.ctx.indent())
                    self._gen_if(out, node, self.ctx.indent(),
                                emit_post_narrowing=False,
                                _skip_source_comment=True)
                    self.ctx.indent_level -= 1
                    out.write(f"{indent}}}\n")
                    return

            lit_snap = self.ctx.save_literal_facts()
            proto_snap = self.ctx.save_protocol_narrowings()
            then_saved = self._emit_isinstance_extractions(out, node.then_type_facts)

            self.ctx.indent_level += 1
            for s in node.then_body:
                self.gen_stmt(out, s)
            self.ctx.emit_block_trailing_comments(out, node.then_body, self.ctx.indent())
            self.ctx.indent_level -= 1

            self.ctx.restore_narrowed_vars(then_saved)
            self.ctx.restore_protocol_narrowings(proto_snap)
            self.ctx.restore_literal_facts(lit_snap)
            self.ctx.restore_local_scope(br_snap)

        # Final else branch (from the last node in the chain)
        last = chain[-1]
        if last.else_body:
            self.ctx.emit_else_comment(out, last.else_body, indent)
            out.write(f"{indent}}} else {{\n")
            # Skip else_type_facts extraction when the else body is an elif
            # that will do its own isinstance checks against the original variant.
            is_elif_continuation = (
                len(last.else_body) == 1
                and isinstance(last.else_body[0], TpyIf)
                and self._is_elif(last, last.else_body[0])
            )
            else_lit_snap = self.ctx.save_literal_facts()
            else_proto_snap = self.ctx.save_protocol_narrowings()
            if is_elif_continuation:
                else_saved: dict[str, str | None] = {}
            else:
                else_saved = self._emit_isinstance_extractions(out, last.else_type_facts)

            self.ctx.indent_level += 1
            for s in last.else_body:
                self.gen_stmt(out, s)
            self.ctx.emit_block_trailing_comments(out, last.else_body, self.ctx.indent())
            self.ctx.indent_level -= 1

            self.ctx.restore_narrowed_vars(else_saved)
            self.ctx.restore_protocol_narrowings(else_proto_snap)
            self.ctx.restore_literal_facts(else_lit_snap)
            self.ctx.restore_local_scope(br_snap)

        out.write(f"{indent}}}\n")

        # Early-return narrowing for recursive unions: when the then-body
        # terminates (return/raise) and there's no else block, code after the
        # if is implicitly the else branch. Emit else_type_facts extractions
        # at the outer scope (like assert narrowing).
        # Limited to recursive unions because general unions may have sequential
        # isinstance checks on the same variable, and the extraction would
        # shadow the original variant for subsequent checks.
        if (emit_post_narrowing
                and not last.else_body and last.else_type_facts
                and self._has_concrete_isinstance_facts(last.else_type_facts)
                and not self._is_protocol_isinstance_condition(last.condition)):
            then_body = last.then_body
            if then_body and isinstance(then_body[-1], (TpyReturn, TpyRaise)):
                # Only emit for recursive union variables
                recursive_facts = {
                    k: v for k, v in last.else_type_facts.items()
                    if self.ctx.is_recursive_union(self.ctx.var_types.get(k))
                }
                if recursive_facts:
                    self._emit_isinstance_extractions(
                        out, recursive_facts, indent_extra=0)

    def _check_overload_return_type(self, stmt: TpyReturn, stub_ret: TpyType) -> bool:
        """Validate that a return expression's type is compatible with the stub's return type.

        Called during @overload specialization codegen. Returns True if
        compatible, False if incompatible (dead code after dead branch
        elimination -- skip the return).

        Delegates to sema's check_type_compatible to reuse all compatibility
        rules (Optional wrapping, inheritance, protocols, coercions, etc.).
        """
        value_type = stmt.value_type
        assert value_type is not None
        # Resolve pending types to concrete types
        if isinstance(value_type, IntLiteralType):
            value_type = BIGINT
        elif isinstance(value_type, PendingViewType):
            value_type = value_type.family.owned_type
        try:
            self.ctx.analyzer.compat.check_type_compatible(
                value_type, stub_ret, "return value",
                loc=stmt.loc, is_return=True,
            )
            return True
        except SemanticError:
            return False

    def _strip_wrong_overload_coerce(self, expr: TpyExpr, stub_ret: TpyType) -> TpyExpr:
        """Strip a TpyCoerce if it targets the wrong type for this overload stub.

        Sema coerces returns against the impl's union return type, which picks
        the first matching member. When generating a specialized stub, that
        coercion may target a different union member than the stub's return
        type. Stripping it lets codegen produce the raw expression, and C++
        implicit conversions handle the rest (e.g., int32_t -> BigInt).
        """
        if not isinstance(expr, TpyCoerce):
            return expr
        if expr.expected_type == stub_ret:
            return expr
        # The coercion targets a type compatible with the stub -- keep it
        # (e.g., coercion to inner type of Optional stub)
        if isinstance(stub_ret, OptionalType) and expr.expected_type == stub_ret.inner:
            return expr
        # Wrong target: unwrap to the raw expression
        return expr.expr

    def _gen_if_overload_specialized(
        self, out: TextIO, chain: list[TpyIf], indent: str,
    ) -> bool:
        """Try to generate an if/elif/else chain with dead branch elimination.

        Returns True if the chain was fully handled (at least one branch
        resolved statically). Returns False if no static resolution was
        possible (caller falls through to normal codegen).
        """
        resolutions = [self._resolve_isinstance_statically(node.condition) for node in chain]
        if all(r is None for r in resolutions):
            return False

        # Collect live (non-False) branches
        live: list[tuple[TpyIf, bool | None]] = []
        for node, resolved in zip(chain, resolutions):
            if resolved is True:
                # Always taken -- emit body directly, skip everything after
                for s in node.then_body:
                    self.gen_stmt(out, s)
                if node.then_body and isinstance(node.then_body[-1], (TpyReturn, TpyRaise)):
                    self.ctx.overload_terminated = True
                return True
            elif resolved is False:
                continue
            else:
                live.append((node, resolved))

        if not live:
            # All branches dead -- emit else body of the last original branch
            last = chain[-1]
            if last.else_body:
                for s in last.else_body:
                    self.gen_stmt(out, s)
            return True

        # Emit only the live (dynamic) branches as a clean if/elif chain
        for node, _ in live:
            self._emit_branch_decls(out, node, indent)
        br_snap = self.ctx.snapshot_local_scope()
        for i, (node, _) in enumerate(live):
            cond = self.expressions.gen_truthy_expr(node.condition)
            self.ctx.temps.flush(out, indent)
            if i == 0:
                out.write(f"{indent}if ({cond}) {{\n")
            else:
                out.write(f"{indent}}} else if ({cond}) {{\n")
            lit_snap = self.ctx.save_literal_facts()
            proto_snap = self.ctx.save_protocol_narrowings()
            then_saved = self._emit_isinstance_extractions(out, node.then_type_facts)
            self.ctx.indent_level += 1
            for s in node.then_body:
                self.gen_stmt(out, s)
            self.ctx.indent_level -= 1
            self.ctx.restore_narrowed_vars(then_saved)
            self.ctx.restore_protocol_narrowings(proto_snap)
            self.ctx.restore_literal_facts(lit_snap)
            self.ctx.restore_local_scope(br_snap)

        # Else body from the last original branch
        last = chain[-1]
        if last.else_body:
            out.write(f"{indent}}} else {{\n")
            self.ctx.indent_level += 1
            for s in last.else_body:
                self.gen_stmt(out, s)
            self.ctx.indent_level -= 1
            self.ctx.restore_local_scope(br_snap)
        out.write(f"{indent}}}\n")
        return True

    @staticmethod
    def _is_elif(outer: TpyIf, inner: TpyIf) -> bool:
        """True when inner is an elif of outer (not a nested else: if).

        Python's AST represents both as orelse=[If(...)]. We distinguish
        them by column: elif keeps the same column, nested else: if is
        indented deeper. Macro-emitted bodies have their locs stripped
        (builder_trace's `_strip_fragment_locs`) so user diagnostics
        don't pick up fragment line numbers; in that case both locs
        are None and we fall back to treating the chain as elif --
        macros emit structurally-equivalent chains and benefit from
        the flat ``else if`` codegen.
        """
        if outer.loc is None and inner.loc is None:
            return True
        if outer.loc is None or inner.loc is None:
            return False
        return inner.loc.column == outer.loc.column

    def _resolve_isinstance_statically(self, condition: TpyExpr) -> bool | None:
        """Check if a condition can be resolved statically in @overload context.

        Returns True if always-true, False if always-false, None if dynamic.
        Handles isinstance checks (union flattening) and literal equality
        checks (literal flattening).
        """
        # Union flattening: isinstance checks on known param types
        if self.ctx.overload_param_types:
            # Direct isinstance: isinstance(x, T)
            if isinstance(condition, TpyCall) and condition.isinstance_var is not None:
                var_name = condition.isinstance_var
                check_type = condition.isinstance_type
                concrete = self.ctx.overload_param_types.get(var_name)
                if concrete is not None and check_type is not None:
                    if concrete == check_type:
                        return True
                    if isinstance(check_type, UnionType) and concrete in check_type.members:
                        return True
                    return False
            # `x is None` / `x is not None` where x is a narrowed param
            if isinstance(condition, TpyBinOp) and condition.op in ("is", "is not"):
                for var_side, none_side in [
                    (condition.left, condition.right),
                    (condition.right, condition.left),
                ]:
                    if (isinstance(var_side, TpyName)
                            and isinstance(none_side, TpyNoneLiteral)
                            and var_side.name in self.ctx.overload_param_types):
                        concrete = self.ctx.overload_param_types[var_side.name]
                        is_none = isinstance(concrete, NoneType)
                        return is_none if condition.op == "is" else not is_none

        # Literal flattening: equality checks and bool truthiness
        if self.ctx.literal_facts:
            result = self._resolve_literal_eq_statically(condition)
            if result is not None:
                return result
            # Bool truthiness: `if x:` where x is Literal[True] or Literal[False]
            if isinstance(condition, TpyName):
                lit_type = self.ctx.literal_facts.get(condition.name)
                if (isinstance(lit_type, LiteralType) and len(lit_type.values) == 1
                        and lit_type.values[0].tag is LiteralTag.BOOL):
                    return bool(lit_type.values[0].value)

        # Logical chains: && / ||
        if isinstance(condition, TpyBinOp) and condition.op in ("&&", "||"):
            left = self._resolve_isinstance_statically(condition.left)
            right = self._resolve_isinstance_statically(condition.right)
            if condition.op == "||":
                if left is True or right is True:
                    return True
                if left is False and right is False:
                    return False
            else:
                if left is False or right is False:
                    return False
                if left is True and right is True:
                    return True
            # Coverage / contradiction on unresolved operands
            if self.ctx.literal_facts and left is None and right is None:
                return self._resolve_literal_chain_statically(condition)
            return None

        # Containment: x in (a, b, ...) / x not in (a, b, ...)
        if (isinstance(condition, TpyBinOp) and condition.op in ("in", "not in")
                and self.ctx.literal_facts):
            result = self._resolve_literal_in_statically(condition)
            if result is not None:
                return result

        # Negated condition
        if isinstance(condition, TpyUnaryOp) and condition.op == "!":
            inner = self._resolve_isinstance_statically(condition.operand)
            if inner is not None:
                return not inner

        return None

    def _resolve_literal_eq_statically(self, condition: TpyExpr) -> bool | None:
        """Resolve `x == lit` / `x != lit` statically using literal_facts.

        Single-value: x == val -> True/False. Multi-value: x == val -> False
        if val not in set (can't resolve True since we don't know which value).
        """
        if not isinstance(condition, TpyBinOp) or condition.op not in ("==", "!="):
            return None
        for var_side, lit_side in [(condition.left, condition.right), (condition.right, condition.left)]:
            if not isinstance(var_side, TpyName):
                continue
            lit_type = self.ctx.literal_facts.get(var_side.name)
            if not isinstance(lit_type, LiteralType):
                continue
            lit_val = literal_value_from_expr(lit_side)
            if lit_val is None:
                continue
            in_set = lit_val in lit_type.values
            if len(lit_type.values) == 1:
                matches = in_set
                return matches if condition.op == "==" else not matches
            # Multi-value: can only resolve when value is NOT in set
            if not in_set:
                return False if condition.op == "==" else True
        return None

    def _resolve_literal_chain_statically(self, condition: TpyBinOp) -> bool | None:
        """Resolve || / && chains of == comparisons using literal_facts."""
        from .expressions import _check_literal_chain
        return _check_literal_chain(condition, self.ctx.literal_facts)

    def _resolve_literal_in_statically(self, condition: TpyBinOp) -> bool | None:
        """Resolve `x in (a, b, ...)` / `x not in (a, b, ...)` using literal_facts."""
        from .expressions import _check_literal_in
        return _check_literal_in(condition, self.ctx.literal_facts)

    def _has_concrete_isinstance_facts(self, type_facts: dict[str, TpyType]) -> bool:
        """Check if type_facts contain any concrete types that would emit extractions."""
        return any(
            not isinstance(ty, (UnionType, NoneType, LiteralType)) and not is_protocol_type(ty)
            for ty in type_facts.values()
        )

    def _emit_branch_decls(self, out: TextIO, stmt: TpyStmt, indent: str) -> None:
        """Pre-declare variables first declared inside if/elif/match branches."""
        branch_decls = self.ctx.analyzer.if_branch_decls.get(id(stmt), {})
        for name, raw_var_type in branch_decls.items():
            var_type = unwrap_ref_type(raw_var_type)
            if (name not in self.ctx.declared_vars
                    and name not in self.ctx.global_declared_vars
                    and name not in self.ctx.native_global_names):
                # @dynamic protocol branch-declared vars: just pre-declare Base* pointer.
                # Per-assignment slots are created by rebind (hoisted to function scope).
                if self._is_dynamic_protocol_type(var_type):
                    base_type = self.protocols.get_dynamic_base_name(var_type)
                    out.write(f"{indent}{base_type}* {name};\n")
                    self.ctx.pointer_locals.add(name)
                    self.ctx.declared_vars.add(name)
                    self.ctx.local_scope_names.add(name)
                    self.ctx.var_types[name] = var_type
                    if self.ctx.current_ns and var_type:
                        self.ctx.current_ns.bind_variable(name, var_type)
                    continue
                # OptionalType uses inner type (pointer-local adds T*).
                # var_type.inner may be ReadonlyType(T) when sema readonly-propagation
                # wrote OptionalType(ReadonlyType(T)) into if_branch_decls.
                # Unwrap both the Optional and the inner ReadonlyType to get the bare C++ type.
                resolve_type = var_type
                is_const = False
                if isinstance(var_type, OptionalType) and var_type.uses_pointer_repr():
                    is_const = isinstance(var_type.inner, ReadonlyType)
                    resolve_type = var_type.inner
                if isinstance(resolve_type, ReadonlyType):
                    is_const = True
                    resolve_type = resolve_type.wrapped
                cpp_type = self.types.type_to_cpp(resolve_type)
                self.ctx.declared_vars.add(name)
                if isinstance(stmt, TpyForEach):
                    self.ctx.loop_hoisted_vars.add(name)
                self.ctx.local_scope_names.add(name)
                self.ctx.var_types[name] = var_type
                if self.ctx.current_ns and var_type:
                    self.ctx.current_ns.bind_variable(name, var_type)
                if self._is_plain_nonvalue(var_type) and name not in self.ctx.reassigned_vars:
                    # Non-value, not reassigned: std::optional<T> avoids
                    # pointer indirection and unnecessary default construction.
                    self.ctx.pointer_locals.add(name)
                    self.ctx.optional_locals.add(name)
                    if is_const:
                        self.ctx.const_indirect_locals.add(name)
                    self.ctx.movable_locals.add(name)
                    out.write(f"{indent}std::optional<{cpp_type}> {name};\n")
                elif self._needs_indirection(var_type, name, None):
                    # Reassigned non-value: T* pointer-local with slot storage.
                    # Mark as branch-hoisted so _gen_pointer_local_rebind puts
                    # rvalue slots into pending_hoist_decls (not block-scoped).
                    self.ctx.pointer_locals.add(name)
                    self.ctx.branch_hoisted_vars.add(name)
                    if is_const:
                        self.ctx.const_indirect_locals.add(name)
                    if name in self.ctx.sema_movable_locals:
                        self.ctx.movable_locals.add(name)
                    const_pfx = "const " if is_const else ""
                    if name in self.ctx.rvalue_reassigned_vars:
                        static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
                        slot = self.ctx.slots.next_slot()
                        self.ctx.rebind_slots[name] = slot
                        out.write(f"{indent}{static_kw}std::optional<{cpp_type}> {slot};\n")
                    out.write(f"{indent}{const_pfx}{cpp_type}* {name};\n")
                else:
                    out.write(f"{indent}{cpp_type} {name};\n")

    def _is_loop_var_hoisted(self, stmt: TpyForEach) -> bool:
        """Check if the loop variable was hoisted for post-loop use."""
        return stmt.hoist_loop_var

    def _gen_while(self, out: TextIO, stmt: TpyWhile, indent: str) -> None:
        """Generate a while loop."""
        has_else = bool(stmt.orelse)
        label = ""
        if has_else:
            label = f"__after_else_{self.ctx.iter_counter}"
            self.ctx.iter_counter += 1
        self.ctx.loop_else_labels.append(label)

        cond = self.expressions.gen_truthy_expr(stmt.condition)
        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}while ({cond}) {{\n")

        lit_snap = self.ctx.save_literal_facts()
        proto_snap = self.ctx.save_protocol_narrowings()
        saved = self._emit_isinstance_extractions(out, stmt.then_type_facts)

        self.ctx.indent_level += 1
        for s in stmt.body:
            self.gen_stmt(out, s)
        self.ctx.emit_block_trailing_comments(out, stmt.body, self.ctx.indent())
        self.ctx.indent_level -= 1

        self.ctx.restore_narrowed_vars(saved)
        self.ctx.restore_protocol_narrowings(proto_snap)
        self.ctx.restore_literal_facts(lit_snap)
        out.write(f"{indent}}}\n")
        self.ctx.loop_else_labels.pop()

        if has_else:
            self.ctx.emit_else_comment(out, stmt.orelse, indent)
            out.write(f"{indent}{{\n")
            self.ctx.indent_level += 1
            for s in stmt.orelse:
                self.gen_stmt(out, s)
            self.ctx.emit_block_trailing_comments(out, stmt.orelse, self.ctx.indent())
            self.ctx.indent_level -= 1
            out.write(f"{indent}}}\n")
            out.write(f"{indent}{label}:;\n")

    def _gen_loop_body(self, out: TextIO, stmt: TpyForEach, indent: str,
                        elem_type: TpyType | None,
                        range_counter: str | None = None,
                        consuming: bool = False) -> None:
        """Generate loop body statements with namespace/scope tracking.

        Shared by _gen_begin_end_loop, _gen_range_counter_loop, and _gen_for_each.
        Writes the body statements, the closing brace, and cleans up the loop
        variable from var_types.

        range_counter: when the loop variable is hoisted, this is the hidden
        counter name; emit `var = counter;` at the start of the body so the
        user variable holds the current (not post-increment) value.

        consuming: when True, the loop variable is bound via auto&& into owned
        storage and can be moved at last use within the iteration body.
        """
        was_declared = stmt.var in self.ctx.declared_vars
        self.ctx.local_scope_names.add(stmt.var)
        self.ctx.declared_vars.add(stmt.var)
        if elem_type:
            self.ctx.var_types[stmt.var] = elem_type
        self.ctx.register_loop_var_storage_form(stmt.var, elem_type, stmt.iterable)
        # Consuming loop: the loop variable is bound via auto&& into owned
        # storage (OwnIter), so it can be std::move'd at last use.
        # Also applies when sema resolved the element type as Own[T] (e.g.
        # iterating over Iterable[Own[T]] parameters).
        is_consuming = consuming or isinstance(stmt.elem_type, OwnType)
        if is_consuming and not stmt.hoist_loop_var:
            self.ctx.movable_locals.add(stmt.var)
        old_ns = self.ctx.current_ns
        if self.ctx.current_ns and elem_type:
            inner_ns = Namespace(parent=self.ctx.current_ns)
            inner_ns.bind_variable(stmt.var, elem_type)
            self.ctx.current_ns = inner_ns
        self.ctx.indent_level += 1
        if range_counter is not None:
            var = escape_cpp_name(stmt.var)
            out.write(f"{self.ctx.indent()}{var} = {range_counter};\n")
        for s in stmt.body:
            self.gen_stmt(out, s)
        self.ctx.emit_block_trailing_comments(out, stmt.body, self.ctx.indent())
        self.ctx.indent_level -= 1
        self.ctx.local_scope_names.discard(stmt.var)
        if is_consuming and not stmt.hoist_loop_var:
            self.ctx.movable_locals.discard(stmt.var)
        # Only undo the loop's flag-add when the var was loop-scoped: hoisted
        # vars survive past the body, and pre-existing vars came in flagged
        # by an earlier site (var-decl) so the flag must persist.
        if not stmt.hoist_loop_var and not was_declared:
            self.ctx.storage_form_tuple_locals.discard(stmt.var)
            self.ctx.const_storage_form_tuple_locals.discard(stmt.var)
            self.ctx.storage_form_optional_locals.discard(stmt.var)
            self.ctx.const_storage_form_optional_locals.discard(stmt.var)
        self.ctx.current_ns = old_ns

        out.write(f"{indent}}}\n")

        if stmt.var in self.ctx.var_types:
            del self.ctx.var_types[stmt.var]
        # Non-hoisted loop vars are scoped to the for block; remove from
        # declared_vars so a later loop reusing the same name can re-declare.
        # Keep if it was already declared before the loop (e.g. global vars).
        if not stmt.hoist_loop_var and not was_declared:
            self.ctx.declared_vars.discard(stmt.var)

    def _gen_begin_end_loop(self, out: TextIO, stmt: TpyForEach, indent: str,
                            iterable_expr: str, elem_type: TpyType,
                            is_lvalue: bool | None = None,
                            consuming: bool = False) -> None:
        """Generate the canonical begin/end iterator loop.

        Produces:
            auto& __obj_N = <lvalue_expr>;   // or: auto __obj_N = <rvalue_expr>;
            auto __beg_N = __obj_N.begin();
            auto __end_N = __obj_N.end();
            for (; __beg_N != __end_N; ++__beg_N) {
                T var = *__beg_N;            // value types: typed copy
                auto&& var = *__beg_N;       // non-value types: forwarding ref
                // body
            }

        If is_lvalue is None, it's determined from stmt.iterable.
        """
        n = self.ctx.iter_counter
        self.ctx.iter_counter += 1
        obj_name = f"__obj_{n}"
        beg_name = f"__beg_{n}"
        end_name = f"__end_{n}"

        if is_lvalue is None:
            is_lvalue = self._is_lvalue_iterable(stmt.iterable)
        obj_binding = "auto&" if is_lvalue else "auto"

        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}{obj_binding} {obj_name} = {iterable_expr};\n")
        out.write(f"{indent}auto {beg_name} = {obj_name}.begin();\n")
        out.write(f"{indent}auto {end_name} = {obj_name}.end();\n")
        out.write(f"{indent}for (; {beg_name} != {end_name}; ++{beg_name}) {{\n")

        inner_indent = indent + INDENT
        cpp_var = escape_cpp_name(stmt.var)
        hoisted = self._is_loop_var_hoisted(stmt)
        binding = loop_var_binding(elem_type, cpp_var, f"*{beg_name}",
                                   stmt.const_loop_var, hoisted,
                                   consuming=consuming)
        out.write(f"{inner_indent}{binding}\n")

        self._gen_loop_body(out, stmt, indent, elem_type, consuming=consuming)

    def _gen_direct_next_loop(self, out: TextIO, stmt: TpyForEach, indent: str,
                               iterable_expr: str, elem_type: TpyType,
                               call: str = ".__next__()",
                               iter_name: str | None = None,
                               consuming: bool = False) -> None:
        """Generate direct for(;;) loop calling a next-method.

        call is the method suffix appended to the iterator name, e.g.:
          ".__next__()"       -- error_return user iterators

        When iter_name is provided, the iterator variable is already allocated
        by the caller and no capture line is emitted.

        Uses ::tpy::unwrap_ref() to unwrap val_or_ref from native_iterator
        __next__(). For user-defined iterators returning plain T, unwrap
        is a transparent pass-through.

        Produces:
            auto& __iter_N = <expr>;   (skipped when iter_name is provided)
            for (;;) {
                auto __r_N = <call>;
                if (!__r_N.has_value()) break;
                T x = ::tpy::unwrap_ref(*__r_N);
                // body
            }
        """
        if iter_name is None:
            n = self.ctx.iter_counter
            self.ctx.iter_counter += 1
            iter_name = f"__iter_{n}"
            r_name = f"__r_{n}"

            src_binding = "auto&" if self._is_lvalue_iterable(stmt.iterable) else "auto"
            self.ctx.temps.flush(out, indent)
            out.write(f"{indent}{src_binding} {iter_name} = {iterable_expr};\n")
        else:
            self.ctx.temps.flush(out, indent)
            n = self.ctx.iter_counter
            self.ctx.iter_counter += 1
            r_name = f"__r_{n}"
        cpp_var = escape_cpp_name(stmt.var)
        out.write(f"{indent}for (;;) {{\n")
        inner_indent = indent + INDENT
        out.write(f"{inner_indent}auto {r_name} = {iter_name}{call};\n")
        out.write(f"{inner_indent}if (!{r_name}.has_value()) break;\n")

        deref = f"::tpy::unwrap_ref(*{r_name})"
        hoisted = self._is_loop_var_hoisted(stmt)
        if hoisted:
            binding = f"{cpp_var} = {deref};"
        elif elem_type:
            binding = loop_var_binding(elem_type, cpp_var, deref,
                                       stmt.const_loop_var,
                                       consuming=consuming)
        else:
            binding = f"auto {cpp_var} = {deref};"
        out.write(f"{inner_indent}{binding}\n")

        self._gen_loop_body(out, stmt, indent, elem_type, consuming=consuming)

    def _gen_direct_next_loop_with_iter(self, out: TextIO, stmt: TpyForEach, indent: str,
                                         iterable_expr: str, elem_type: TpyType,
                                         iter_call: str = ".__iter__",
                                         next_call: str = ".__next__()") -> None:
        """Call __iter__() on source, then direct loop on the resulting iterator.

        iter_call: how to get the iterator (".__iter__" for method, "::tpy::__iter__" for free fn)
        next_call: how to advance (".__next__()")
        """
        n = self.ctx.iter_counter
        self.ctx.iter_counter += 1
        src_name = f"__src_{n}"
        iter_name = f"__itr_{n}"
        src_binding = "auto&" if self._is_lvalue_iterable(stmt.iterable) else "auto"

        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}{src_binding} {src_name} = {iterable_expr};\n")
        # auto&& preserves reference returns from __iter__ (iterator-shaped
        # sources return self& -- needed for move-only owning iterators and
        # in-place consumption of user iterators) and lifetime-extends value
        # returns (container -> native_iterator fallback).
        if iter_call.startswith("."):
            out.write(f"{indent}auto&& {iter_name} = {src_name}{iter_call}();\n")
        else:
            out.write(f"{indent}auto&& {iter_name} = {iter_call}({src_name});\n")

        self._gen_direct_next_loop(out, stmt, indent, iterable_expr, elem_type,
                                   call=next_call, iter_name=iter_name)

    @staticmethod
    def _unwrap_coerce(expr: TpyExpr) -> TpyExpr:
        """Unwrap TpyCoerce nodes to get the underlying expression."""
        while isinstance(expr, TpyCoerce):
            expr = expr.expr
        return expr

    def _is_lvalue_iterable(self, expr: TpyExpr) -> bool:
        """Check if the iterable expression is a C++ lvalue."""
        return is_lvalue_iterable(
            expr, self.ctx.analyzer.registry.get_record,
            self.types.get_resolved_type)

    @staticmethod
    def _is_literal_range_arg(expr: TpyExpr) -> bool:
        """Check if a range arg is a compile-time literal (safe to inline).

        Only literals can be inlined in the for-loop condition. Variable names
        must be pre-evaluated into temps because Python's range() captures args
        at call time, but the for-loop condition re-evaluates each iteration.
        """
        return StatementGenerator._extract_int_literal(expr) is not None

    _FIXED_INT_NAMES = frozenset(str(t) for t in ALL_FIXED_INTS)

    @staticmethod
    def _extract_int_literal(expr: TpyExpr) -> int | None:
        """Extract a compile-time integer value from a range argument.

        Handles bare literals (3), negated literals (-3), and fixed-int
        constructor calls with a literal arg (Int32(3)).
        Returns the integer value or None if not a compile-time constant.
        """
        expr = StatementGenerator._unwrap_coerce(expr)
        if isinstance(expr, TpyIntLiteral):
            return expr.value
        if isinstance(expr, TpyUnaryOp) and expr.op == '-' and isinstance(expr.operand, TpyIntLiteral):
            return -expr.operand.value
        # Int32(3), UInt8(10), etc. — constructor call with a single literal arg
        if (isinstance(expr, TpyCall) and len(expr.args) == 1
                and isinstance(expr.func, TpyName) and expr.func_name in StatementGenerator._FIXED_INT_NAMES):
            inner = StatementGenerator._unwrap_coerce(expr.args[0])
            if isinstance(inner, TpyIntLiteral):
                return inner.value
            if isinstance(inner, TpyUnaryOp) and inner.op == '-' and isinstance(inner.operand, TpyIntLiteral):
                return -inner.operand.value
        return None

    def _gen_range_counter_loop(self, out: TextIO, stmt: TpyForEach,
                                 indent: str, elem_type: TpyType) -> bool:
        """Optimize range() to a C-style for-loop.

        Returns True if the optimization was applied, False if the caller
        should fall back to the generic while-loop codegen.
        """
        assert isinstance(stmt.iterable, TpyCall) and stmt.iterable.func_name == "range"
        range_call = stmt.iterable
        nargs = len(range_call.args)

        # Classify the step from the original AST (unwrap TpyCoerce from sema)
        if nargs == 3:
            step_lit = self._extract_int_literal(range_call.args[2])
            if step_lit is not None:
                if step_lit == 0:
                    return False  # zero step panics at runtime -- use Range ctor
                elif step_lit > 0:
                    step_kind = "literal_pos"
                    step_val = step_lit
                else:
                    step_kind = "literal_neg"
                    step_val = step_lit
            else:
                step_kind = "variable"
                step_val = None
        else:
            step_kind = "plus_one"
            step_val = 1

        gen_args = self.builtins.gen_range_args(range_call)

        # Determine start/stop/step C++ expressions
        if nargs == 1:
            start_expr, stop_expr = "0", gen_args[0]
        elif nargs == 2:
            start_expr, stop_expr = gen_args[0], gen_args[1]
        else:
            start_expr, stop_expr = gen_args[0], gen_args[1]

        n = self.ctx.iter_counter
        self.ctx.iter_counter += 1

        self.ctx.temps.flush(out, indent)

        var = escape_cpp_name(stmt.var)
        cpp_elem = elem_type.to_cpp()
        # If loop var was pre-declared (hoisted for post-loop use), use a
        # hidden counter and assign the user variable inside the body so it
        # holds the last-yielded value (not the post-increment overshoot).
        hoisted = self._is_loop_var_hoisted(stmt)
        if hoisted:
            counter = f"__range_{n}"
            var_decl = f"{cpp_elem} {counter}"
        else:
            counter = var
            var_decl = f"{cpp_elem} {var}"

        # Pre-evaluate non-literal args into temps (left-to-right, matching
        # Python's argument evaluation order).  Literals are safe to inline
        # since they can't change; everything else must be captured once.
        if nargs >= 2:
            start_arg_ast = range_call.args[0]
            if not self._is_literal_range_arg(start_arg_ast):
                temp_name = f"__start_{n}"
                out.write(f"{indent}{cpp_elem} {temp_name} = {start_expr};\n")
                start_expr = temp_name

        stop_arg_ast = range_call.args[0] if nargs == 1 else range_call.args[1]
        if not self._is_literal_range_arg(stop_arg_ast):
            temp_name = f"__stop_{n}"
            out.write(f"{indent}{cpp_elem} {temp_name} = {stop_expr};\n")
            stop_expr = temp_name

        if step_kind == "plus_one":
            out.write(f"{indent}for ({var_decl} = {start_expr}; "
                      f"{counter} < {stop_expr}; ++{counter}) {{\n")
        elif step_kind == "literal_pos":
            if step_val == 1:
                out.write(f"{indent}for ({var_decl} = {start_expr}; "
                          f"{counter} < {stop_expr}; ++{counter}) {{\n")
            else:
                step_cpp = gen_args[2]
                if is_big_int_type(elem_type):
                    step_temp = f"__step_{n}"
                    out.write(f"{indent}{cpp_elem} {step_temp} = {step_cpp};\n")
                    step_cpp = step_temp
                self._gen_range_overflow_check(out, indent, start_expr, stop_expr, step_cpp, elem_type)
                out.write(f"{indent}for ({var_decl} = {start_expr}; "
                          f"{counter} < {stop_expr}; "
                          f"{counter} += {step_cpp}) {{\n")
        elif step_kind == "literal_neg":
            if step_val == -1:
                out.write(f"{indent}for ({var_decl} = {start_expr}; "
                          f"{counter} > {stop_expr}; --{counter}) {{\n")
            else:
                step_cpp = gen_args[2]
                if is_big_int_type(elem_type):
                    step_temp = f"__step_{n}"
                    out.write(f"{indent}{cpp_elem} {step_temp} = {step_cpp};\n")
                    step_cpp = step_temp
                self._gen_range_overflow_check(out, indent, start_expr, stop_expr, step_cpp, elem_type)
                out.write(f"{indent}for ({var_decl} = {start_expr}; "
                          f"{counter} > {stop_expr}; "
                          f"{counter} += {step_cpp}) {{\n")
        else:
            # Variable step -- capture, zero-check, upfront overflow check, ternary condition
            step_cpp = gen_args[2]
            step_temp = f"__step_{n}"
            out.write(f"{indent}{cpp_elem} {step_temp} = {step_cpp};\n")
            step_cpp = step_temp
            out.write(f'{indent}::tpy::range_check_step_nonzero({step_cpp});\n')
            self._gen_range_overflow_check(out, indent, start_expr, stop_expr, step_cpp, elem_type)
            out.write(f"{indent}for ({var_decl} = {start_expr}; "
                      f"{step_cpp} > 0 ? {counter} < {stop_expr} : {counter} > {stop_expr}; "
                      f"{counter} += {step_cpp}) {{\n")

        self._gen_loop_body(out, stmt, indent, elem_type,
                            range_counter=counter if hoisted else None)
        return True

    def _gen_range_overflow_check(self, out: TextIO, indent: str,
                                    start_expr: str, stop_expr: str,
                                    step_expr: str, elem_type: TpyType) -> None:
        """Emit upfront overflow check for fixed-int range loops with step != ±1."""
        if is_fixed_int_type(elem_type):
            cpp_t = elem_type.to_cpp()
            out.write(f"{indent}::tpy::range_check_overflow<{cpp_t}>({start_expr}, {stop_expr}, {step_expr});\n")

    def _gen_for_each(self, out: TextIO, stmt: TpyForEach, indent: str) -> None:
        """Generate a for-each loop over a collection or iterator.

        Dispatch is handled by _gen_for_each_loop; see its docstring for
        the full dispatch order.
        """
        # Generator body: lower for-loops with yields to while-loops
        if self.ctx.in_generator_body and id(stmt) in self.ctx.generator_for_loop_info:
            has_else = bool(stmt.orelse)
            label = ""
            if has_else:
                label = f"__after_else_{self.ctx.iter_counter}"
                self.ctx.iter_counter += 1
            self.ctx.loop_else_labels.append(label)

            self._gen_generator_for_loop(out, stmt, indent)

            self.ctx.loop_else_labels.pop()
            if has_else:
                self.ctx.emit_else_comment(out, stmt.orelse, indent)
                out.write(f"{indent}{{\n")
                self.ctx.indent_level += 1
                for s in stmt.orelse:
                    self.gen_stmt(out, s)
                self.ctx.emit_block_trailing_comments(out, stmt.orelse, self.ctx.indent())
                self.ctx.indent_level -= 1
                out.write(f"{indent}}}\n")
                out.write(f"{indent}{label}:;\n")
            return

        has_else = bool(stmt.orelse)
        label = ""
        if has_else:
            label = f"__after_else_{self.ctx.iter_counter}"
            self.ctx.iter_counter += 1
        self.ctx.loop_else_labels.append(label)

        self._gen_for_each_loop(out, stmt, indent)
        self.ctx.loop_else_labels.pop()

        if has_else:
            self.ctx.emit_else_comment(out, stmt.orelse, indent)
            out.write(f"{indent}{{\n")
            self.ctx.indent_level += 1
            for s in stmt.orelse:
                self.gen_stmt(out, s)
            self.ctx.emit_block_trailing_comments(out, stmt.orelse, self.ctx.indent())
            self.ctx.indent_level -= 1
            out.write(f"{indent}}}\n")
            out.write(f"{indent}{label}:;\n")

    def _gen_for_each_loop(self, out: TextIO, stmt: TpyForEach, indent: str) -> None:
        """Generate the loop part of a for-each (without else handling).

        Dispatch order (peepholes first, then universal default):
        - Enum iteration: range over EnumUtil::members (begin/end).
        - OwnIter[T] / CopyIter[T]: begin/end (already exposes begin/end).
        - Auto-consuming iteration (`consuming_iter_fi`): native or user path.
        - range(...) call: C-style counter loop (or Range<T> begin/end fallback).
        - Concrete NativeIterable type, or NativeIterable[T]/Spannable[T]
          protocol param: plain C++ begin/end range-for, skipping the
          native_iterator adapter. Spannable[T] works because the compiler
          synthesizes begin()/end() from __span__() for conforming types.
          NativeIterable[T] as a parameter type is typically used as an
          opt-in fast path via `Iterable[T] | NativeIterable[T]` + isinstance
          narrowing.
        - Universal default: auto&& __itr = ::tpy::__iter__(src); for(;;) __itr.__next__().
          Handles all remaining shapes uniformly -- protocol Iterator/Iterable,
          error_return __next__ iterators, user __iter__() methods, and
          move-only owning iterators (map/filter/zip results).
        """
        # Enum iteration: `for c in Color` -> range over EnumUtil<Color>::members
        if stmt.enum_iterable is not None:
            enum_type = stmt.enum_iterable
            cpp_type = enum_type.to_cpp()
            iterable = f"::tpy::EnumUtil<{cpp_type}>::members"
            self._gen_begin_end_loop(out, stmt, indent, iterable, enum_type)
            return

        from tpyc.modules import is_native_iterable
        iterable_type = unwrap_ref_type(self.types.get_resolved_type(stmt.iterable))

        # Resolve sema-stored elem_type (handles PendingViewType -> concrete).
        # Strip Ref -- codegen loop binding handles reference semantics via
        # is_value_type() / loop_var_binding(), not through Ref.
        sema_elem = unwrap_ref_type(self.types.resolve_type(stmt.elem_type)) if stmt.elem_type else None

        # OwnIter / CopyIter: explicit own_iter() / copy_iter() call.
        # These have begin/end, so use standard begin/end loop.
        # OwnIter uses auto&& binding (move-ready for future per-element moves).
        from ..type_def_registry import is_own_iter, is_copy_iter, is_list
        if is_own_iter(iterable_type) or is_copy_iter(iterable_type):
            iterable = self.expressions.gen_expr(stmt.iterable)
            elem_type = sema_elem
            assert elem_type is not None
            consuming = is_own_iter(iterable_type)
            self._gen_begin_end_loop(out, stmt, indent, iterable, elem_type,
                                     consuming=consuming)
            return

        # Auto-consuming iteration: iterable at last use with consuming __iter__.
        # Only triggers when the loop variable is mutated (not const_loop_var).
        # Uses shared _gen_consuming_iter (also used by call-site arg generation).
        # Skip when loop var is hoisted (used after loop) -- the hoisted var may
        # be a view (string_view) into the container, so the container must stay alive.
        if stmt.consuming_iter_fi is not None and not stmt.hoist_loop_var:
            iterable = self.expressions.gen_expr(stmt.iterable)
            consuming_call = self.expressions._gen_consuming_iter(stmt.iterable, iterable)
            if consuming_call is not None:
                elem_type = sema_elem
                assert elem_type is not None
                if stmt.consuming_iter_fi.native_name:
                    # Native consuming iter (e.g. tpy::own_iter) returns a C++ range
                    self._gen_begin_end_loop(out, stmt, indent, consuming_call, elem_type,
                                             consuming=True, is_lvalue=False)
                else:
                    # User-defined consuming __iter__ returns a TPy Iterator
                    n = self.ctx.iter_counter
                    self.ctx.iter_counter += 1
                    iter_name = f"__itr_{n}"
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}auto {iter_name} = {consuming_call};\n")
                    self._gen_direct_next_loop(out, stmt, indent, "", elem_type,
                                               call=".__next__()", iter_name=iter_name,
                                               consuming=True)
                return

        # Resolve TypeParamRef to its bound for protocol-based iteration
        resolved_type = iterable_type
        if isinstance(iterable_type, TypeParamRef):
            bound = self.ctx.current_type_param_bounds.get(iterable_type.name)
            if bound is not None and is_protocol_type(bound):
                resolved_type = bound

        # Optimize range() calls to C-style counter loops
        if isinstance(stmt.iterable, TpyCall) and stmt.iterable.func_name == "range":
            elem_type = sema_elem
            if elem_type and self._gen_range_counter_loop(out, stmt, indent, elem_type):
                return
            # Counter optimization didn't apply; fall back to Range<T> begin/end
            iterable = self.expressions.gen_expr_deref(stmt.iterable)
            if elem_type:
                self._gen_begin_end_loop(out, stmt, indent, iterable, elem_type, is_lvalue=False)
                return

        # NativeIterable peephole: built-in types (list, dict, set, Span,
        # Array, str, bytes, etc.) and NativeIterable[T] / Spannable[T]
        # protocol params use C++ range-based-for with begin/end. User
        # records are NOT NativeIterable (they use the universal
        # __iter__+__next__ default). Spannable[T] protocol params work
        # because the compiler synthesizes begin()/end() from __span__()
        # for concrete types that satisfy Spannable (see records.py).
        # NativeIterable[T] is typically used as the fast-path arm of an
        # `Iterable[T] | NativeIterable[T]` union narrowed with
        # `isinstance(x, NativeIterable)`: the narrowed branch hits this
        # peephole (range-for), the other branch falls to the universal
        # default (__iter__/__next__).
        is_native = (
            is_native_iterable(iterable_type, registry=self.ctx.analyzer.registry)
            or (is_protocol_type(resolved_type)
                and resolved_type.qualified_name() in ("tpy.NativeIterable", "tpy.Spannable"))
        )
        if is_native:
            iterable = self.expressions.gen_expr_deref(stmt.iterable)
            if isinstance(stmt.iterable, TpyStrLiteral):
                # C string literals include the null terminator, so wrap in string_view
                iterable = f"std::string_view({iterable})"
            assert sema_elem is not None, "sema should always resolve for-loop element type"
            elem_type = sema_elem
            if isinstance(elem_type, IntLiteralType):
                elem_type = self.ctx.analyzer.ctx.default_int_type
            self._gen_begin_end_loop(out, stmt, indent, iterable, elem_type)
            return

        # Universal default: ::tpy::__iter__(src) + .__next__() loop.
        # Covers protocol-typed Iterator[T] and Iterable[T], error_return
        # __next__ iterators, user records with __iter__() returning a
        # separate iterator type, and iterator-shaped sources (map/filter/
        # zip results, user iterator records). The runtime's ::tpy::__iter__
        # dispatches to the user's __iter__() method (or the auto-synthesized
        # one for pure iterators). `auto&&` binding in _gen_direct_next_loop_with_iter
        # preserves reference returns so move-only owning iterators work and
        # user iterator consumption semantics are preserved.
        iterable = self.expressions.gen_expr_deref(stmt.iterable)
        assert sema_elem is not None, "sema should always resolve for-loop element type"
        elem_type = sema_elem
        if isinstance(elem_type, IntLiteralType):
            elem_type = self.ctx.analyzer.ctx.default_int_type
        self._gen_direct_next_loop_with_iter(out, stmt, indent, iterable, elem_type,
                                              iter_call="::tpy::__iter__",
                                              next_call=".__next__()")
