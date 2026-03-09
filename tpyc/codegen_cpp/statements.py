"""
TurboPython Statement Code Generation

Generates C++ code from TurboPython statements.
"""

from __future__ import annotations
import io
from typing import Callable, TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, FloatType, BoolType,
    ArrayType, ListType, PendingListType, PendingDictType, PendingSetType, PendingStrType, OwnType, OptionalType,
    NoneType, NamedType, StrType, StringType, StrViewType, STR, TupleType,
    INT32, BIGINT, is_protocol_type, FixedIntType, ALL_FIXED_INTS,
    ReadonlyType, unwrap_readonly, unwrap_optional_own, TypeParamRef, UnionType, EnumType,
    local_var_is_movable, resolve_int_literals,
)
from ..parse import (
    TpyStmt, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign, TpyDelItem, TpyExprStmt, TpyReturn,
    TpyIf, TpyWhile, TpyForEach, TpyBreak, TpyContinue, TpyPassStmt, TpyRaiseStopIteration,
    TpyGlobal,
    TpyImport, TpySubscript, TpyStrLiteral, TpyNoneLiteral, TpyName, TpyExpr, TpyFunction,
    TpyAssert, TpyBoolLiteral,
    TpyFieldAccess, TpyMethodCall,
    TpyCall, TpyIntLiteral, TpyUnaryOp, TpyCoerce, TpyIfExpr,
    TpyMatch, TpyMatchCase, TpyPattern, TpyWildcardPattern, TpyCapturePattern,
    TpyClassPattern, TpyLiteralPattern, TpyValuePattern, TpyOrPattern, TpyAsPattern,
)
from ..namespace import Namespace
from collections import defaultdict

from .context import INDENT, CodeGenError, escape_cpp_name, escape_cpp_string, escape_cpp_char, qualified_cpp_name, expand_cpp_template
from .type_resolution import resolve_stmt_binding_type
from ..prescan import match_is_none
from .string_dispatch import find_best_discriminator, STRING_SWITCH_THRESHOLD

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
    ):
        self.ctx = ctx
        self.types = types
        self.builtins = builtins
        self.protocols = protocols
        # Will be set after expressions is created
        self.expressions: ExpressionGenerator | None = None
        self._reassigned_param_copies: list[tuple[str, TpyType]] = []

    def set_expressions(self, expressions: ExpressionGenerator):
        """Set expressions generator (to break circular dependency)."""
        self.expressions = expressions

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
                if isinstance(ptype, OptionalType) and isinstance(ptype.inner, StrType):
                    init = (f"{param_ref} ? std::make_optional("
                            f"std::string(*{param_ref})) : std::nullopt")
                else:
                    init = param_ref
                body_buf.write(f"{indent}{cpp_type} {cpp_name} = {init};\n")
            self._reassigned_param_copies = []
        for stmt in stmts:
            if track_stmt_line:
                self.ctx.current_stmt_line = stmt.loc.line if hasattr(stmt, 'loc') and stmt.loc else 0
            self.gen_stmt(body_buf, stmt)
        if track_stmt_line:
            self.ctx.current_stmt_line = 0
        hoist_prefix = INDENT * (self.ctx.indent_level - 1)
        for decl in self.ctx.pending_hoist_decls:
            out.write(f"{hoist_prefix}{decl}")
        out.write(body_buf.getvalue())

    def gen_body(self, out: TextIO, body: list[TpyStmt],
                 params: list[tuple[str, TpyType]], return_type: TpyType,
                 func: TpyFunction, local_ns: Namespace,
                 indent_level: int = 1, is_method: bool = False,
                 record_type_param_bounds: dict[str, TpyType] | None = None) -> None:
        """Generate the body of a function or method.

        Handles scope setup, body buffering, hoist-decl prepending, and cleanup.
        Shared by gen_function_def() and _gen_method().
        """
        self.ctx.reset_scope()
        self.ctx.declared_vars = {pname for pname, _ in params}
        self.ctx.var_types = {pname: ptype for pname, ptype in params}
        self.ctx.local_scope_names = {pname for pname, _ in params}
        self.ctx.global_declared_vars = self.ctx.analyzer.function_global_decls.get(id(func), set())
        scan = self.ctx.analyzer.function_scan_results.get(id(func))
        if scan:
            self.ctx.reassigned_vars = scan.reassigned - self.ctx.global_declared_vars
            self.ctx.rvalue_reassigned_vars = scan.rvalue_reassigned - self.ctx.global_declared_vars
            self.ctx.lvalue_reassigned_vars = scan.lvalue_reassigned - self.ctx.global_declared_vars
        else:
            self.ctx.reassigned_vars = set()
            self.ctx.rvalue_reassigned_vars = set()
            self.ctx.lvalue_reassigned_vars = set()
        self.ctx.hoisted_vars = self.ctx.analyzer.function_hoisted_vars.get(id(func), set())
        self.ctx.move_through_vars = self.ctx.analyzer.function_move_through_vars.get(id(func), set())
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
            # Own[T] and Own[T] | None params are movable (caller gave up ownership)
            own_actual = unwrap_optional_own(actual)
            if own_actual is not None and not own_actual.wrapped.is_value_type():
                self.ctx.movable_locals.add(pname)
                # T&& forwarding refs only apply to bare Own[T] free function template params
                # (where T is deduced at the call site). Own[T] | None shouldn't use
                # std::forward<T> and class method params use T by value.
                if (isinstance(actual, OwnType)
                        and isinstance(own_actual.wrapped, TypeParamRef) and not is_method):
                    self.ctx.forwarding_params[pname] = own_actual.wrapped.name
        self.ctx.current_ns = local_ns
        self.ctx.indent_level = indent_level
        self.ctx.current_return_type = return_type
        self.ctx.current_func_params = {pname: ptype for pname, ptype in params}
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

        self._gen_buffered_body(out, body)
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
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._gen_if(out, stmt, indent)
        elif isinstance(stmt, TpyWhile):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._gen_while(out, stmt, indent)
        elif isinstance(stmt, TpyForEach):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._gen_for_each(out, stmt, indent)
        elif isinstance(stmt, TpyAssert):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._gen_assert(out, stmt, indent)
        elif isinstance(stmt, TpyTupleUnpack):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._gen_tuple_unpack(out, stmt, indent)
        elif isinstance(stmt, TpyMatch):
            self.ctx.emit_source_comment(out, stmt.loc, indent)
            self._gen_match(out, stmt, indent)
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
                if stmt.then_type_facts:
                    for var_name, narrowed_type in stmt.then_type_facts.items():
                        self.ctx.assign_narrowed_types[var_name] = narrowed_type

    def _gen_simple_stmt(self, stmt: TpyStmt, indent: str) -> str | None:
        """Generate code for simple statements. Returns code to write or None.

        Expression generation happens here (which may create temps).
        The caller handles flushing temps before writing the returned code.
        """
        if isinstance(stmt, TpyVarDecl):
            return self._gen_var_decl_code(stmt, indent)
        elif isinstance(stmt, TpyAssign):
            return self._gen_assign_code(stmt, indent)
        elif isinstance(stmt, TpyAugAssign):
            return self._gen_aug_assign_code(stmt, indent)
        elif isinstance(stmt, TpyDelItem):
            return self._gen_del_item_code(stmt, indent)
        elif isinstance(stmt, TpyExprStmt):
            if isinstance(stmt.expr, TpyStrLiteral):
                return None  # Skip docstrings
            return f"{indent}{self.expressions.gen_expr(stmt.expr)};\n"
        elif isinstance(stmt, TpyReturn):
            if stmt.value:
                ret_type = self.ctx.current_return_type
                if isinstance(ret_type, OptionalType):
                    if not ret_type.uses_pointer_repr():
                        # Value-type Optional: return std::nullopt or plain value
                        if isinstance(stmt.value, TpyNoneLiteral):
                            return f"{indent}return std::nullopt;\n"
                        ret_expr = self.expressions.gen_expr_deref(stmt.value, ret_type)
                        return f"{indent}return {ret_expr};\n"
                    # Non-value Optional: return pointer (not dereferenced)
                    if isinstance(stmt.value, TpyNoneLiteral):
                        return f"{indent}return nullptr;\n"
                    ret_expr = self.expressions.gen_expr(stmt.value, ret_type)
                    if self.ctx.is_indirect_name(stmt.value):
                        # Already a pointer -- return as-is
                        return f"{indent}return {ret_expr};\n"
                    if isinstance(stmt.value, TpyIfExpr):
                        # Ternary already produces T* via _ptr_optional_branch
                        return f"{indent}return {ret_expr};\n"
                    # Field access with non-value Optional produces std::optional<T>, convert to T*
                    if isinstance(stmt.value, TpyFieldAccess):
                        val_type = self.ctx.get_expr_type(stmt.value)
                        if isinstance(val_type, OptionalType) and val_type.uses_pointer_repr():
                            return f"{indent}return tpy::optional_to_ptr({ret_expr});\n"
                    # Take address of lvalue
                    return f"{indent}return &({ret_expr});\n"
                ret_expr = self.expressions.gen_expr(
                    stmt.value, ret_type)
                # Dereference pointer-locals/pointer-globals on return (T* -> T&)
                if self.ctx.is_indirect_name(stmt.value):
                    ret_expr = f"(*{ret_expr})"
                    ret_expr = self.expressions._maybe_move(stmt.value, ret_expr)
                # Unwrap value-optional expressions when return type is non-Optional.
                # The sema narrows the type inside `if x is not None:` branches,
                # but the C++ variable/field is still std::optional<T>.
                elif (
                    not isinstance(ret_type, OptionalType)
                    and self._is_value_optional_expr(stmt.value)
                ):
                    analyzed_type = self.ctx.get_expr_type(stmt.value)
                    if isinstance(analyzed_type, OptionalType):
                        ret_expr = f"tpy::deref_optional_check({ret_expr})"
                    else:
                        ret_expr = f"(*{ret_expr})"
                        # Narrowed Optional[str] param: (*s) yields string_view
                        if (isinstance(ret_type, StrType)
                                and self._is_optional_str_param(stmt.value)):
                            ret_expr = f"std::string({ret_expr})"
                # StrView local returned as str needs explicit conversion.
                # Also wrap str-typed params (which are string_view in C++).
                elif isinstance(ret_type, StrType):
                    expr_type = self.types.get_resolved_type(stmt.value)
                    if isinstance(expr_type, StrViewType):
                        ret_expr = f"std::string({ret_expr})"
                    elif (isinstance(expr_type, StrType)
                          and isinstance(stmt.value, TpyName)
                          and stmt.value.name in self.ctx.current_func_params):
                        ret_expr = f"std::string({ret_expr})"
                    elif (isinstance(expr_type, StrType)
                          and self._expr_uses_optional_str_param(stmt.value)):
                        ret_expr = f"std::string({ret_expr})"
                # Consuming method: move self fields on return (this->field is lvalue)
                if (self.ctx.in_consuming_method
                        and isinstance(stmt.value, TpyFieldAccess)
                        and isinstance(stmt.value.obj, TpyName)
                        and stmt.value.obj.name == "self"):
                    ret_expr = f"std::move({ret_expr})"
                return f"{indent}return {ret_expr};\n"
            return f"{indent}return;\n"
        elif isinstance(stmt, TpyBreak):
            return f"{indent}break;\n"
        elif isinstance(stmt, TpyContinue):
            return f"{indent}continue;\n"
        elif isinstance(stmt, TpyPassStmt):
            return ""  # No-op - emit nothing
        elif isinstance(stmt, TpyGlobal):
            return ""  # No C++ output -- just a sema directive
        elif isinstance(stmt, TpyRaiseStopIteration):
            return f"{indent}return std::nullopt;\n"
        elif isinstance(stmt, TpyImport):
            # Only emit __tpy_init() for actual user modules (not builtins)
            # Check is_builtin flag to handle single-file/REPL mode where builtins
            # are still in user_module_imports
            module_info = self.ctx.analyzer.registry.get_module(stmt.module_name)
            is_builtin = module_info and module_info.is_builtin
            if stmt.module_name in self.ctx.user_module_imports and not is_builtin:
                result = ""
                # For dotted imports, emit parent package inits first (Python semantics)
                # e.g., "mypackage.utils" -> init mypackage first, then mypackage.utils
                parts = stmt.module_name.split('.')
                for i in range(1, len(parts)):
                    parent_pkg = '.'.join(parts[:i])
                    if parent_pkg == self.ctx.module_name:
                        continue  # don't self-init
                    if parent_pkg in self.ctx.all_user_modules:
                        result += f"{indent}{qualified_cpp_name(parent_pkg, '__tpy_init')}();\n"
                # Then init the submodule itself (skip self-init)
                if stmt.module_name != self.ctx.module_name:
                    result += f"{indent}{qualified_cpp_name(stmt.module_name, '__tpy_init')}();\n"
                return result
            return ""  # Builtin module - no init needed
        return None

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
        if target_type.is_value_type():
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
        return False

    def _is_dynamic_protocol_type(self, target_type: TpyType | None) -> bool:
        """Check if the type is a @dynamic protocol (needs adapter slot codegen)."""
        if target_type is None or not is_protocol_type(target_type):
            return False
        protocol_info = self.ctx.analyzer.registry.get_protocol(target_type.name)
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

    def _gen_dynamic_protocol_init(self, name: str, target_type: NamedType,
                                    init: 'TpyExpr', indent: str) -> str:
        """Generate slot + pointer-local for a @dynamic protocol variable.

        If the concrete type directly inherits the protocol base, emit a plain
        concrete slot (no adapter). Otherwise use adapter wrapping.
        If the init is already an erased protocol variable, just copy the pointer.
        Uses brace init to avoid C++ most-vexing-parse with constructor calls.
        """
        concrete_type = self.ctx.get_expr_type(init)
        proto_name = target_type.name
        base_type = self.protocols.get_dynamic_base_name(proto_name)

        if is_protocol_type(concrete_type):
            # Already erased -- copy the pointer
            init_expr = self.expressions.gen_expr_deref(init, concrete_type)
            return f"{indent}{base_type}* {name} = &{init_expr};\n"

        concrete_cpp = self.types.type_to_cpp(concrete_type)
        init_slot = self.ctx.slots.next_slot()
        init_expr = self.expressions.gen_expr(init, concrete_type)

        if self.protocols.directly_implements_dynamic(concrete_type, proto_name):
            # Direct inheritance -- plain concrete slot, implicit upcast
            slot_type = concrete_cpp
        else:
            # Structural conformance -- adapter wrapping
            slot_type = self.protocols.get_dynamic_adapter_type(proto_name, concrete_cpp)

        return (f"{indent}{slot_type} {init_slot}{{{init_expr}}};\n"
                f"{indent}{base_type}* {name} = &{init_slot};\n")

    def _gen_dynamic_protocol_rebind(self, name: str, target_type: NamedType,
                                      init: 'TpyExpr', indent: str) -> str:
        """Generate slot rebind for a @dynamic protocol variable reassignment.

        Slots are hoisted to function scope via pending_hoist_decls so they
        survive block scopes (if/else branches, loops).
        """
        concrete_type = self.ctx.get_expr_type(init)
        proto_name = target_type.name

        if is_protocol_type(concrete_type):
            # Already erased -- rebind pointer to same object
            init_expr = self.expressions.gen_expr_deref(init, concrete_type)
            return f"{indent}{name} = &{init_expr};\n"

        concrete_cpp = self.types.type_to_cpp(concrete_type)
        rebind_slot = self.ctx.slots.next_slot()
        init_expr = self.expressions.gen_expr(init, concrete_type)

        if self.protocols.directly_implements_dynamic(concrete_type, proto_name):
            slot_type = concrete_cpp
        else:
            slot_type = self.protocols.get_dynamic_adapter_type(proto_name, concrete_cpp)

        # Hoist slot to function scope (survives block scopes).
        # Global scope (__tpy_init) needs 'static' so slots outlive the function.
        static_kw = "static " if self.ctx.slots.global_scope else ""
        self.ctx.pending_hoist_decls.append(
            f"  {static_kw}std::optional<{slot_type}> {rebind_slot};\n"
        )
        return (f"{indent}{rebind_slot}.emplace({init_expr});\n"
                f"{indent}{name} = &*{rebind_slot};\n")

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
            # Strip ReadonlyType -- C++ doesn't need it on locals
            target_type = unwrap_readonly(target_type)
            if isinstance(target_type, OwnType):
                target_type = target_type.wrapped
            target_type = resolve_int_literals(target_type, self.ctx.analyzer.ctx.default_int_for_literal)
            resolved = self._resolve_pending_container(target_type)
            if resolved is not None:
                target_type = resolved
            elif isinstance(target_type, PendingStrType):
                info = self.ctx.analyzer.ctx.str_vars.get(target_type.str_var_id)
                target_type = info.resolved_type if info and info.resolved_type else STR
        return target_type

    def _resolve_pending_container(self, typ: TpyType) -> TpyType | None:
        """Resolve PendingListType/PendingDictType/PendingSetType to their resolved concrete type.

        Returns None if the type is not a pending container or has no resolution yet.
        """
        sema = self.ctx.analyzer.ctx
        if isinstance(typ, PendingListType):
            info = sema.list_literals.get(typ.literal_id)
            if info and info.resolved_type:
                return info.resolved_type
        elif isinstance(typ, PendingDictType):
            info = sema.dict_literals.get(typ.literal_id)
            if info and info.resolved_type:
                return info.resolved_type
        elif isinstance(typ, PendingSetType):
            info = sema.set_literals.get(typ.literal_id)
            if info and info.resolved_type:
                return info.resolved_type
        return None

    def _normalize_decl_type_for_cpp(self, var_type: TpyType) -> TpyType:
        """Normalize declaration type before C++ emission."""
        resolve_lit = self.ctx.analyzer.ctx.default_int_for_literal
        resolved = self._resolve_pending_container(var_type)
        if resolved is not None:
            var_type = resolved
        elif isinstance(var_type, PendingListType):
            # Fallback for unresolved list: resolve IntLiteralType in element
            elem = var_type.element_type
            if isinstance(elem, IntLiteralType):
                var_type = ListType(resolve_lit(elem))
        elif isinstance(var_type, PendingStrType):
            info = self.ctx.analyzer.ctx.str_vars.get(var_type.str_var_id)
            var_type = info.resolved_type if info and info.resolved_type else STR
        # Resolve IntLiteralType in all composite types (tuples, arrays, lists)
        var_type = resolve_int_literals(var_type, resolve_lit)
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
            return self._cpp_decl_type(stmt.type)
        elif stmt.init:
            resolved_type = resolve_stmt_binding_type(
                stmt,
                self.ctx.analyzer,
                include_global_binding=(self.ctx.current_ns is self.ctx.analyzer.global_ns),
            )
            if resolved_type is None or isinstance(resolved_type, (PendingListType, PendingDictType, PendingSetType, PendingStrType)):
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
            return f"tpy::optional_to_ptr({slot} = {init_expr})"
        if is_optional_slot:
            return f"&*({slot} = {init_expr})"
        return f"&({slot} = {init_expr})"

    @staticmethod
    def _ptr_from_local_slot(slot: str, is_opt_field: bool) -> str:
        """Derive pointer from an inline-declared slot."""
        if is_opt_field:
            return f"tpy::optional_to_ptr({slot})"
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
                is_hoisted = name in self.ctx.hoisted_vars
                if is_hoisted:
                    self.ctx.pending_hoist_decls.append(f"  {hoist_static_kw}{slot_opt_cpp} {init_slot};\n")
                    if name in self.ctx.rvalue_reassigned_vars:
                        rebind_slot = self.ctx.slots.next_slot()
                        self.ctx.rebind_slots[name] = rebind_slot
                        self.ctx.pending_hoist_decls.append(f"  {hoist_static_kw}{slot_opt_cpp} {rebind_slot};\n")
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
                    self.ctx.pending_hoist_decls.append(f"  {hoist_static_kw}{slot_opt_cpp} {slot};\n")
                else:
                    rebind_decl = f"{indent}{static_kw}{slot_opt_cpp} {slot};\n"
            return f"{rebind_decl}{indent}{const_pfx}{cpp_type}* {name} = nullptr;\n"

        init_type = self.ctx.get_expr_type(init)
        # Optional non-value field on lvalue object -> optional_to_ptr directly
        # Optional non-value non-field source -> T* pass-through
        # Optional non-value field on rvalue -> falls through to rvalue path
        is_opt_field = (isinstance(init_type, OptionalType)
                        and init_type.uses_pointer_repr()
                        and isinstance(init, TpyFieldAccess))
        if isinstance(init_type, OptionalType) and init_type.uses_pointer_repr():
            if isinstance(init, TpyFieldAccess):
                if not self.ctx.is_rvalue_source(init):
                    init_expr = self.expressions.gen_expr(init, target_type)
                    return f"{indent}{const_pfx}{cpp_type}* {name} = tpy::optional_to_ptr({init_expr});\n"
                # rvalue field: fall through to rvalue path
            else:
                init_expr = self.expressions.gen_expr(init, target_type)
                return f"{indent}{const_pfx}{cpp_type}* {name} = {init_expr};\n"

        init_expr = self.expressions.gen_expr(init, target_type)

        is_hoisted = name in self.ctx.hoisted_vars
        static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
        # Hoisted decls go to function scope -- use global_scope flag from slot state
        hoist_static_kw = "static " if self.ctx.slots.global_scope else ""
        slot_opt_cpp = f"std::optional<{cpp_type}>"
        target = f"{const_pfx}{cpp_type}* {name}"
        if self.ctx.is_rvalue_source(init):
            init_slot = self.ctx.slots.next_slot()
            slot_type = self._slot_decl_type(cpp_type, is_opt_field)
            if is_hoisted:
                self.ctx.pending_hoist_decls.append(f"  {hoist_static_kw}{slot_opt_cpp} {init_slot};\n")
                if name in self.ctx.rvalue_reassigned_vars:
                    rebind_slot = self.ctx.slots.next_slot()
                    self.ctx.rebind_slots[name] = rebind_slot
                    self.ctx.pending_hoist_decls.append(f"  {hoist_static_kw}{slot_opt_cpp} {rebind_slot};\n")
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
                self.ctx.pending_hoist_decls.append(f"  {hoist_static_kw}{slot_opt_cpp} {slot};\n")
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

        init_type = self.ctx.get_expr_type(init)
        # Optional non-value field on lvalue -> optional_to_ptr directly
        # Optional non-value non-field source -> T* pass-through
        # Optional non-value field on rvalue -> falls through to rvalue path
        is_opt_field = (isinstance(init_type, OptionalType)
                        and init_type.uses_pointer_repr()
                        and isinstance(init, TpyFieldAccess))
        if isinstance(init_type, OptionalType) and init_type.uses_pointer_repr():
            if isinstance(init, TpyFieldAccess):
                if not self.ctx.is_rvalue_source(init):
                    init_expr = self.expressions.gen_expr(init, target_type)
                    return f"{indent}{cpp_name} = tpy::optional_to_ptr({init_expr});\n"
                # rvalue field: fall through to rvalue path
            else:
                init_expr = self.expressions.gen_expr(init, target_type)
                return f"{indent}{cpp_name} = {init_expr};\n"

        init_expr = self.expressions.gen_expr(init, target_type)

        is_hoisted = name in self.ctx.hoisted_vars
        static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
        hoist_static_kw = "static " if self.ctx.slots.global_scope else ""
        slot_opt_cpp = f"std::optional<{cpp_type}>"
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
                self.ctx.pending_hoist_decls.append(f"  {hoist_static_kw}{slot_opt_cpp} {slot};\n")
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

    def _gen_var_decl_code(self, stmt: TpyVarDecl, indent: str) -> str | None:
        """Generate code for a variable declaration. Returns code to write or None."""
        from ..parse.nodes import VarLinkage
        if stmt.linkage != VarLinkage.DEFAULT:
            return None
        # Final globals are defined at namespace scope, skip in __tpy_init
        if stmt.is_final:
            return None

        cpp_name = escape_cpp_name(stmt.name)

        # Global-declared vars: emit assignment to the existing global, not a local decl
        if stmt.name in self.ctx.global_declared_vars:
            if not stmt.init:
                return None
            var_type = self.ctx.get_expr_type(stmt.init)
            init_expr = self.expressions.gen_expr(stmt.init, var_type)
            target_name = self.ctx.native_global_names.get(stmt.name, stmt.name)
            return f"{indent}{target_name} = {init_expr};\n"

        # Check if variable is already declared (reassignment)
        if stmt.name in self.ctx.declared_vars:
            if stmt.init:
                var_type = self.ctx.var_types.get(stmt.name)
                # Pointer-local reassignment
                if stmt.name in self.ctx.pointer_locals:
                    # @dynamic protocol reassignment: new adapter slot + rebind
                    if self._is_dynamic_protocol_type(var_type):
                        return self._gen_dynamic_protocol_rebind(stmt.name, var_type, stmt.init, indent)
                    # OptionalType uses inner type (pointer-local adds T*)
                    resolve_type = var_type
                    if isinstance(var_type, OptionalType) and var_type.uses_pointer_repr():
                        resolve_type = var_type.inner
                    cpp_type = self.types.type_to_cpp(resolve_type) if resolve_type else "auto"
                    return self._gen_pointer_local_rebind(stmt.name, cpp_type, stmt.init, var_type, indent)
                init_expr = self.expressions.gen_expr(stmt.init, var_type)
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

        cpp_type = self._resolve_cpp_type(stmt)

        # @dynamic protocol types always use adapter slots + Base* pointer-local
        if self._is_dynamic_protocol_type(target_type):
            assert stmt.init, f"@dynamic protocol local '{stmt.name}' requires initializer"
            self.ctx.pointer_locals.add(stmt.name)
            return self._gen_dynamic_protocol_init(stmt.name, target_type, stmt.init, indent)

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
                if local_var_is_movable(
                        stmt.name,
                        self.ctx.hoisted_vars,
                        self.ctx.reassigned_vars,
                        self.ctx.lvalue_reassigned_vars,
                        stmt.init is None or self.ctx.is_rvalue_source(stmt.init)):
                    self.ctx.movable_locals.add(stmt.name)
                if stmt.init:
                    return self._gen_pointer_local_init(stmt.name, cpp_type, stmt.init, target_type, indent)
                else:
                    # Optional without initializer -> nullptr
                    return f"{indent}{const_pfx}{cpp_type}* {cpp_name} = nullptr;\n"
            else:
                # T& reference -- alias without rebinding
                init_expr = self.expressions.gen_expr_deref(stmt.init, target_type)
                return f"{indent}{const_pfx}{cpp_type}& {cpp_name} = {init_expr};\n"

        # Tier 1 non-value-type locals are eligible for auto-move at last use
        init_is_rvalue = stmt.init is None or self.ctx.is_rvalue_source(stmt.init)
        if stmt.name in self.ctx.move_through_vars:
            init_is_rvalue = True
        if (target_type and not target_type.is_value_type()
                and local_var_is_movable(
                    stmt.name,
                    self.ctx.hoisted_vars,
                    self.ctx.reassigned_vars,
                    self.ctx.lvalue_reassigned_vars,
                    init_is_rvalue)):
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
                    init_expr = f"tpy::deref_optional_check({init_expr})"
                else:
                    init_expr = f"(*{init_expr})"
            # string_view -> string init requires explicit conversion in C++
            elif isinstance(target_type, StrType):
                init_resolved = self.types.get_resolved_type(stmt.init)
                if isinstance(init_resolved, StrViewType):
                    init_expr = f"std::string({init_expr})"
            return f"{indent}{cpp_type} {cpp_name} = {init_expr};\n"
        else:
            return f"{indent}{cpp_type} {cpp_name};\n"

    def _gen_assign_code(self, stmt: TpyAssign, indent: str) -> str:
        """Generate code for an assignment. Returns code to write."""
        # Clear stale assignment narrowing on reassignment
        if isinstance(stmt.target, TpyName):
            self.ctx.assign_narrowed_types.pop(stmt.target.name, None)
        # Special handling for subscript assignment
        if isinstance(stmt.target, TpySubscript):
            obj = self.expressions.gen_expr(stmt.target.obj)
            target_type = self.ctx.get_expr_type(stmt.target)
            value = self.expressions.gen_expr(stmt.value, target_type)
            value = self.expressions._maybe_move(stmt.value, value)
            obj_type = self.ctx.get_expr_type(stmt.target.obj)
            index_type = self.ctx.analyzer.get_expr_type(stmt.target.index)
            # Dereference globals for subscript access
            subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(stmt.target.obj) else obj
            index_expr = self.expressions.gen_index_expr(stmt.target.index, index_type)

            # Bounds-safe: index provably in [0, len(obj)), skip normalize_index
            if stmt.target.bounds_safe:
                return f"{indent}{subscript_obj}[{index_expr}] = {value};\n"

            # Use registry lookup for __setitem__
            cpp_template = self.builtins.get_type_method_template(obj_type, "__setitem__")
            if cpp_template:
                code = expand_cpp_template(cpp_template, subscript_obj, index_expr, value)
                return f"{indent}{code};\n"
            else:
                return f"{indent}tpy::__setitem__({subscript_obj}, {index_expr}, {value});\n"

        # Pointer-local rebinding (e.g., x.field = ... where x is pointer-local handled by field access)
        if isinstance(stmt.target, TpyName) and stmt.target.name in self.ctx.pointer_locals:
            target_type = self.ctx.var_types.get(stmt.target.name)
            cpp_type = self.types.type_to_cpp(target_type) if target_type else "auto"
            return self._gen_pointer_local_rebind(stmt.target.name, cpp_type, stmt.value, target_type, indent)

        # Assignment to optional field: std::optional<T> storage needs boundary conversion
        if isinstance(stmt.target, TpyFieldAccess):
            target_type = self.ctx.get_expr_type(stmt.target)
            if isinstance(target_type, OptionalType) and target_type.uses_pointer_repr():
                target = self.expressions.gen_expr(stmt.target)
                # Value source is T* (pointer-local, function returning Optional) -> wrap
                if self.ctx.is_indirect_name(stmt.value):
                    value = self.expressions.gen_expr(stmt.value)
                    return f"{indent}{target} = tpy::ptr_to_optional({value});\n"
                raw_val_type = self.ctx.get_expr_type(stmt.value)
                val_type = raw_val_type.wrapped if isinstance(raw_val_type, OwnType) else raw_val_type
                source = self.ctx.unwrap_copy(stmt.value)
                if isinstance(val_type, OptionalType) and not isinstance(source, TpyFieldAccess):
                    # Own[T] | None returns std::optional<T> -- direct assign
                    # T | None returns T* -- needs ptr_to_optional wrapping
                    is_owned_optional = (isinstance(val_type, OptionalType)
                                         and isinstance(val_type.inner, OwnType))
                    value = self.expressions.gen_expr(stmt.value, target_type)
                    if is_owned_optional:
                        value = self.expressions._maybe_move(stmt.value, value)
                        return f"{indent}{target} = {value};\n"
                    return f"{indent}{target} = tpy::ptr_to_optional({value});\n"
                # Direct value or optional-to-optional (field-to-field) works without conversion
                value = self.expressions.gen_expr_deref(stmt.value, target_type)
                value = self.expressions._maybe_move(stmt.value, value)
                return f"{indent}{target} = {value};\n"

        # Default: simple assignment (includes field assignments like self.x = val)
        target = self.expressions.gen_expr(stmt.target)
        target_type = self.ctx.get_expr_type(stmt.target)
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

            cpp_template = self.builtins.get_type_method_template(obj_type, "__delitem__")
            if cpp_template:
                code = expand_cpp_template(cpp_template, subscript_obj, index_expr)
                parts.append(f"{indent}{code};\n")
            else:
                parts.append(f"{indent}tpy::__delitem__({subscript_obj}, {index_expr});\n")
        return "".join(parts)

    def _gen_aug_assign_code(self, stmt: TpyAugAssign, indent: str) -> str:
        """Generate code for an augmented assignment. Returns code to write."""
        # Special handling for subscript targets - use set_value() pattern
        if isinstance(stmt.target, TpySubscript):
            return self._gen_aug_assign_subscript_code(stmt, indent)

        # In-place operator (__iadd__, __ior__, etc.) -- mutates target directly
        if inplace := stmt.resolved_inplace:
            target = self.expressions.gen_expr(stmt.target)
            if self.ctx.is_indirect_name(stmt.target):
                target = f"(*{target})"
            value = self.expressions.gen_expr_deref(stmt.value)
            if inplace.method.cpp_template:
                result = self.expressions._gen_binop_from_result(inplace, target, value)
                return f"{indent}{result};\n"
            else:
                # User-defined in-place method (no cpp_template)
                return f"{indent}{target}.{inplace.method.name}({value});\n"

        target = self.expressions.gen_expr(stmt.target)
        target_type = self.ctx.get_expr_type(stmt.target)
        value = self.expressions.gen_expr(stmt.value, target_type)
        value_type = self.types.get_resolved_type(stmt.value, target_type)

        # Special case: FixedInt += BigInt should convert BigInt to the target type
        # This preserves checked arithmetic and avoids unnecessary promotion to BigInt
        if isinstance(target_type, Int32Type) and isinstance(value_type, BigIntType):
            # Dereference globals before .to_fixed_check<T>() conversion
            if self.ctx.is_indirect_name(stmt.value):
                value = f"(*{value})"
            value = f"({value}).to_fixed_check<{target_type.to_cpp()}>()"
            value_type = target_type

        # Use resolved binop from sema for augmented assignment (a += b is a = a + b)
        if binop_result := stmt.resolved_binop:
            result = self.expressions._gen_binop_from_result(binop_result, target, value)
            return f"{indent}{target} = {result};\n"
        else:
            # Fallback for operators not in module system
            cpp_op = "/" if stmt.op == "//" else stmt.op
            return f"{indent}{target} {cpp_op}= {value};\n"

    def _gen_aug_assign_subscript_code(self, stmt: TpyAugAssign, indent: str) -> str:
        """Generate code for augmented assignment to subscript targets.

        Uses set_value(container, index, get_value(container, index) op value) pattern
        for range-checked read and write. Only supported for value type elements.
        """
        if not isinstance(stmt.target, TpySubscript):
            raise CodeGenError("Expected subscript target for augmented assignment", stmt.loc)
        subscript = stmt.target
        obj = self.expressions.gen_expr(subscript.obj)
        obj_type = self.types.get_resolved_type(subscript.obj)
        index_type = self.ctx.get_expr_type(subscript.index)
        # Dereference globals for subscript access
        subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(subscript.obj) else obj
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
        get_template = self.builtins.get_type_method_template(obj_type, "__getitem__")
        if get_template:
            read_expr = expand_cpp_template(get_template, subscript_obj, index_expr)
        else:
            read_expr = f"{subscript_obj}[{index_expr}]"

        value = self.expressions.gen_expr(stmt.value, elem_type)
        value_type = self.types.get_resolved_type(stmt.value, elem_type)

        # Special case: FixedInt += BigInt should convert BigInt to the element type
        if isinstance(elem_type, Int32Type) and isinstance(value_type, BigIntType):
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
        set_template = self.builtins.get_type_method_template(obj_type, "__setitem__")
        if set_template:
            code = expand_cpp_template(set_template, subscript_obj, index_expr, result_expr)
            return f"{indent}{code};\n"
        else:
            return f"{indent}tpy::__setitem__({subscript_obj}, {index_expr}, {result_expr});\n"

    def _is_optional_str_param(self, expr: TpyExpr) -> bool:
        """Check if expr is an Optional[str] function parameter (string_view in C++)."""
        if not isinstance(expr, TpyName):
            return False
        declared = self.ctx.current_func_params.get(expr.name)
        return (isinstance(declared, OptionalType)
                and isinstance(declared.inner, StrType))

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
            # Protocol isinstance narrows the concept constraint, not the value;
            # no std::get extraction needed (the variable is already a T& ref).
            if is_protocol_type(narrowed_type):
                continue
            cpp_type = self.types.type_to_cpp(narrowed_type)
            var_ref = var_name
            if var_name in self.ctx.narrowed_vars:
                var_ref = self.ctx.narrowed_vars[var_name]
            elif self.ctx.is_indirect_name(TpyName(var_name)):
                var_ref = f"(*{var_name})"
            local_name = f"__{var_name}"
            # Value-type union params are const&, so std::get yields const T&.
            # Non-value union params and locals are mutable.
            var_decl_type = self.ctx.var_types.get(var_name)
            is_const = (var_name in self.ctx.current_func_params
                        and var_decl_type is not None and var_decl_type.is_value_type())
            qualifier = "const auto&" if is_const else "auto&"
            out.write(f"{inner_indent}{qualifier} {local_name} = std::get<{cpp_type}>({var_ref});\n")
            saved[var_name] = self.ctx.narrowed_vars.get(var_name)
            self.ctx.narrowed_vars[var_name] = local_name
        return saved

    def _restore_narrowed_vars(self, saved: dict[str, str | None]) -> None:
        """Restore narrowed_vars after a branch block."""
        for var_name, prev in saved.items():
            if prev is not None:
                self.ctx.narrowed_vars[var_name] = prev
            else:
                self.ctx.narrowed_vars.pop(var_name, None)

    def _gen_tuple_unpack(self, out: TextIO, stmt: TpyTupleUnpack, indent: str) -> None:
        """Generate tuple unpacking: auto __tup_N = expr; T a = std::get<0>(...); ..."""
        value_expr = self.expressions.gen_expr(stmt.value)
        self.ctx.temps.flush(out, indent)

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
        if isinstance(stmt.value, TpyName) and not any(stmt.is_owned):
            const_kw = "" if any(stmt.is_ref) else "const "
            out.write(f"{indent}{const_kw}auto& {tmp} = {value_expr};\n")
        else:
            out.write(f"{indent}auto {tmp} = {value_expr};\n")

        for i, name in enumerate(stmt.targets):
            if name is None:
                continue
            target_type = stmt.target_types[i]
            cpp_type = self.types.type_to_cpp(target_type)
            cpp_name = escape_cpp_name(name)
            get_expr = f"std::get<{i}>({tmp})"
            if stmt.is_owned[i]:
                get_expr = f"std::move({get_expr})"

            if stmt.is_new[i]:
                self.ctx.declared_vars.add(name)
                self.ctx.local_scope_names.add(name)
                self.ctx.var_types[name] = target_type
                if stmt.is_ref[i]:
                    if name in self.ctx.reassigned_vars or name in self.ctx.hoisted_vars:
                        self.ctx.pointer_locals.add(name)
                        out.write(f"{indent}{cpp_type}* {cpp_name} = "
                                  f"&{get_expr};\n")
                    else:
                        out.write(f"{indent}{cpp_type}& {cpp_name} = "
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
                        is_hoisted = name in self.ctx.hoisted_vars
                        if is_hoisted:
                            hoist_kw = "static " if self.ctx.slots.global_scope else ""
                            slot_opt = f"std::optional<{cpp_type}>"
                            self.ctx.pending_hoist_decls.append(
                                f"  {hoist_kw}{slot_opt} {slot};\n")
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

    def _gen_assert(self, out: TextIO, stmt: TpyAssert, indent: str) -> None:
        """Generate an assert statement with optional isinstance union narrowing."""
        # Constant-fold trivially-known assertions (no temps to flush).
        if isinstance(stmt.condition, TpyBoolLiteral):
            if stmt.condition.value:
                return
            if stmt.message is not None and isinstance(stmt.message, TpyStrLiteral):
                msg = stmt.message.value.replace("\\", "\\\\").replace('"', '\\"')
                out.write(f'{indent}tpy::tpy_panic("{msg}");\n')
                return
            out.write(f'{indent}tpy::tpy_panic("assertion failed");\n')
            return
        if isinstance(stmt.condition, TpyNoneLiteral):
            if stmt.message is not None and isinstance(stmt.message, TpyStrLiteral):
                msg = stmt.message.value.replace("\\", "\\\\").replace('"', '\\"')
                out.write(f'{indent}tpy::tpy_panic("{msg}");\n')
                return
            out.write(f'{indent}tpy::tpy_panic("assertion failed");\n')
            return
        bool_cond = self.expressions.gen_truthy_expr(stmt.condition)
        self.ctx.temps.flush(out, indent)
        if stmt.message is not None and isinstance(stmt.message, TpyStrLiteral):
            msg = stmt.message.value.replace("\\", "\\\\").replace('"', '\\"')
            out.write(f'{indent}if (!({bool_cond})) tpy::tpy_panic("{msg}");\n')
        else:
            out.write(f'{indent}if (!({bool_cond})) tpy::tpy_panic("assertion failed");\n')
        # Emit std::get<T> extractions for isinstance-narrowed union variables.
        # Unlike if-branch narrowing, assert narrowing persists for the rest of scope,
        # so we do NOT call _restore_narrowed_vars.
        self._emit_isinstance_extractions(out, stmt.then_type_facts, indent_extra=0)

    def _gen_match(self, out: TextIO, stmt: TpyMatch, indent: str) -> None:
        """Generate a match/case statement. Uses switch when possible, if/elif otherwise."""
        assert stmt.subject_type is not None
        subject_type = unwrap_readonly(stmt.subject_type)

        # Pre-declare variables first declared inside match arms
        self._emit_branch_decls(out, stmt, indent)

        # Evaluate subject and bind to a local
        subject_code = self.expressions.gen_expr(stmt.subject)
        self.ctx.temps.flush(out, indent)
        # Use auto& for variables (safe reference), auto for temporaries (avoid dangling)
        binding = "auto&" if isinstance(stmt.subject, TpyName) else "auto"
        out.write(f"{indent}{binding} __match_subject = {subject_code};\n")

        if isinstance(subject_type, UnionType):
            has_guard = any(c.guard is not None for c in stmt.cases)
            if has_guard:
                self._gen_match_guarded_union(out, stmt, subject_type, indent)
            else:
                self._gen_match_switch_union(out, stmt, subject_type, indent)
        elif isinstance(subject_type, EnumType):
            self._gen_match_switch_enum(out, stmt, indent)
        elif isinstance(subject_type, (FixedIntType, BoolType)):
            self._gen_match_switch_primitive(out, stmt, indent)
        elif isinstance(subject_type, NamedType) and subject_type.is_user_record:
            has_guard = any(c.guard is not None for c in stmt.cases)
            if has_guard:
                self._gen_match_guarded_record(out, stmt, indent)
            else:
                self._gen_match_if_elif_record(out, stmt, indent)
        elif isinstance(subject_type, OptionalType):
            partition = self._partition_optional_cases(stmt.cases)
            if partition is not None:
                none_cases, inner_cases = partition
                self._gen_match_optimized_optional(
                    out, stmt, subject_type, none_cases, inner_cases, indent,
                )
            else:
                self._gen_match_if_elif_optional(out, stmt, subject_type, indent)
        elif isinstance(subject_type, (StrType, StringType, StrViewType)):
            if self._should_switch_str(stmt):
                self._gen_match_switch_str(out, stmt, indent)
            else:
                self._gen_match_if_elif(out, stmt, indent)
        else:
            self._gen_match_if_elif(out, stmt, indent)

    def _gen_match_switch_union(
        self, out: TextIO, stmt: TpyMatch, subject_type: UnionType, indent: str,
    ) -> None:
        """Generate switch (__match_subject.index()) for union subjects."""
        inner = INDENT * (self.ctx.indent_level + 1)
        out.write(f"{indent}switch (__match_subject.index()) {{\n")

        for i, case in enumerate(stmt.cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            pattern, as_name, as_raw_name = self._unwrap_as_pattern(case.pattern)

            if isinstance(pattern, TpyClassPattern):
                assert pattern.resolved_type is not None
                idx = self._variant_index(subject_type, pattern.resolved_type)
                out.write(f"{indent}case {idx}: {{\n")
                case_var: str | None = None
                if pattern.keywords or case.type_facts:
                    case_var = f"__case_{i}"
                    out.write(f"{inner}auto& {case_var} = std::get<{idx}>(__match_subject);\n")
                if pattern.keywords:
                    self._gen_match_field_bindings(out, pattern, case_var, inner)
                if as_name is not None:
                    if as_raw_name and as_raw_name in self.ctx.declared_vars:
                        out.write(f"{inner}{as_name} = {case_var if case_var else f'std::get<{idx}>(__match_subject)'};\n")
                    else:
                        src = case_var if case_var else f"std::get<{idx}>(__match_subject)"
                        out.write(f"{inner}auto& {as_name} = {src};\n")
                # Narrowing
                saved_narrow: dict[str, str | None] = {}
                if case.type_facts:
                    for var_name in case.type_facts:
                        saved_narrow[var_name] = self.ctx.narrowed_vars.get(var_name)
                        self.ctx.narrowed_vars[var_name] = case_var
                self.ctx.indent_level += 1
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 1
                self._restore_narrowed_vars(saved_narrow)
                out.write(f"{inner}break;\n")
                out.write(f"{indent}}}\n")

            elif isinstance(pattern, TpyOrPattern):
                has_bindings = any(
                    isinstance(alt, TpyClassPattern) and alt.keywords
                    for alt in pattern.patterns
                )
                has_wildcard = any(
                    isinstance(alt, (TpyWildcardPattern, TpyCapturePattern))
                    for alt in pattern.patterns
                )
                if has_wildcard:
                    # Wildcard subsumes all alternatives -> default
                    out.write(f"{indent}default: {{\n")
                    self.ctx.indent_level += 1
                    for s in case.body:
                        self.gen_stmt(out, s)
                    self.ctx.indent_level -= 1
                    out.write(f"{inner}break;\n")
                    out.write(f"{indent}}}\n")
                elif not has_bindings:
                    # No bindings: case fallthrough
                    for alt in pattern.patterns:
                        assert isinstance(alt, TpyClassPattern) and alt.resolved_type is not None
                        idx = self._variant_index(subject_type, alt.resolved_type)
                        out.write(f"{indent}case {idx}:\n")
                    out.write(f"{indent}{{\n")
                    self.ctx.indent_level += 1
                    for s in case.body:
                        self.gen_stmt(out, s)
                    self.ctx.indent_level -= 1
                    out.write(f"{inner}break;\n")
                    out.write(f"{indent}}}\n")
                else:
                    # With bindings: body duplication per alternative
                    for j, alt in enumerate(pattern.patterns):
                        assert isinstance(alt, TpyClassPattern) and alt.resolved_type is not None
                        idx = self._variant_index(subject_type, alt.resolved_type)
                        out.write(f"{indent}case {idx}: {{\n")
                        case_var = f"__case_{i}_{j}"
                        out.write(f"{inner}auto& {case_var} = std::get<{idx}>(__match_subject);\n")
                        if alt.keywords:
                            self._gen_match_field_bindings(out, alt, case_var, inner)
                        saved = self._apply_narrowing(case.type_facts, case_var)
                        self.ctx.indent_level += 1
                        for s in case.body:
                            self.gen_stmt(out, s)
                        self.ctx.indent_level -= 1
                        self._restore_narrowed_vars(saved)
                        out.write(f"{inner}break;\n")
                        out.write(f"{indent}}}\n")

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                self._gen_switch_default_arm(out, pattern, as_name, as_raw_name, case.body, indent, inner)

            else:
                raise CodeGenError(f"Unsupported pattern in union switch: {type(pattern).__name__}")

        out.write(f"{indent}}}\n")

    def _gen_match_switch_enum(self, out: TextIO, stmt: TpyMatch, indent: str) -> None:
        """Generate switch (__match_subject) for enum subjects."""
        groups = self._group_switch_arms(stmt, kind="enum")
        self._emit_switch_groups(out, groups, indent)

    def _gen_match_switch_primitive(self, out: TextIO, stmt: TpyMatch, indent: str) -> None:
        """Generate switch (__match_subject) for int/bool subjects."""
        groups = self._group_switch_arms(stmt, kind="primitive")
        self._emit_switch_groups(out, groups, indent)

    # Entry in a switch arm group:
    # (guard, body, capture_escaped, as_escaped, raw_names, loc)
    # raw_names: set of raw Python names for declared_vars lookup
    _SwitchEntry = tuple[
        TpyExpr | None, list['TpyStmt'], str | None, str | None, set[str],
        'SourceLocation | None',
    ]

    def _group_switch_arms(
        self, stmt_or_cases: 'TpyMatch | list[TpyMatchCase]', kind: str,
    ) -> list[tuple[list[str], list[_SwitchEntry]]]:
        """Group match cases by switch label for enum/primitive subjects.

        Returns a list of (labels, entries) where:
        - labels: list of case label strings, or ["default"] for wildcard
        - entries: list of (guard, body, cap_escaped, as_escaped, raw_names, loc)
        Same-value cases with guards are merged into a single group.
        Entries are ordered guarded-first, unguarded-last (enforced by sema
        duplicate-case check which only allows same-value repeats with guards).
        """
        cases: list[TpyMatchCase] = (
            stmt_or_cases if isinstance(stmt_or_cases, list) else stmt_or_cases.cases
        )
        groups: dict[str, tuple[list[str], list[StatementGenerator._SwitchEntry]]] = {}
        default_entries: list[StatementGenerator._SwitchEntry] = []

        for case in cases:
            pattern, as_escaped, as_raw = self._unwrap_as_pattern(case.pattern)
            raw_names: set[str] = set()
            if as_raw is not None:
                raw_names.add(as_raw)

            if isinstance(pattern, (TpyValuePattern, TpyLiteralPattern)):
                if kind == "enum":
                    assert isinstance(pattern, TpyValuePattern)
                    label = self.expressions.gen_expr(pattern.expr)
                else:
                    assert isinstance(pattern, TpyLiteralPattern)
                    label = self._switch_literal_label(pattern)
                entry: StatementGenerator._SwitchEntry = (
                    case.guard, case.body, None, as_escaped, raw_names, case.loc,
                )
                if label in groups:
                    groups[label][1].append(entry)
                else:
                    groups[label] = ([label], [entry])

            elif isinstance(pattern, TpyOrPattern):
                has_wild = any(
                    isinstance(alt, (TpyWildcardPattern, TpyCapturePattern))
                    for alt in pattern.patterns
                )
                if has_wild:
                    default_entries.append((case.guard, case.body, None, as_escaped, raw_names, case.loc))
                else:
                    labels = []
                    for alt in pattern.patterns:
                        if kind == "enum":
                            assert isinstance(alt, TpyValuePattern)
                            labels.append(self.expressions.gen_expr(alt.expr))
                        else:
                            assert isinstance(alt, TpyLiteralPattern)
                            labels.append(self._switch_literal_label(alt))
                    key = "|".join(labels)
                    entry = (case.guard, case.body, None, as_escaped, raw_names, case.loc)
                    if key in groups:
                        groups[key][1].append(entry)
                    else:
                        groups[key] = (labels, [entry])

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                cap_escaped = escape_cpp_name(pattern.name) if isinstance(pattern, TpyCapturePattern) else None
                if isinstance(pattern, TpyCapturePattern):
                    raw_names.add(pattern.name)
                default_entries.append((case.guard, case.body, cap_escaped, as_escaped, raw_names, case.loc))

            else:
                raise CodeGenError(f"Unsupported pattern in {kind} switch: {type(pattern).__name__}")

        result = list(groups.values())
        if default_entries:
            result.append((["default"], default_entries))
        return result

    def _emit_switch_groups(
        self, out: TextIO,
        groups: list[tuple[list[str], list[_SwitchEntry]]],
        indent: str,
        subject_expr: str = "__match_subject",
    ) -> None:
        """Emit a switch statement from grouped arms."""
        inner = INDENT * (self.ctx.indent_level + 1)

        # Check if any non-default group needs guard fallback to default
        has_default = any(labels == ["default"] for labels, _ in groups)
        needs_default_goto = has_default and any(
            labels != ["default"]
            and all(g is not None for g, _, _, _, _, _ in entries)
            for labels, entries in groups
        )
        default_label: str | None = None
        if needs_default_goto:
            self.ctx.match_counter += 1
            default_label = f"__match_default_{self.ctx.match_counter}"

        out.write(f"{indent}switch ({subject_expr}) {{\n")

        for labels, entries in groups:
            # Emit source comment for first entry in group
            if entries:
                self.ctx.emit_source_comment(out, entries[0][5], indent)
            # Emit case labels
            if labels == ["default"]:
                if default_label is not None:
                    out.write(f"{indent}default: {default_label}: {{\n")
                else:
                    out.write(f"{indent}default: {{\n")
            elif len(labels) == 1:
                out.write(f"{indent}case {labels[0]}: {{\n")
            else:
                for label in labels:
                    out.write(f"{indent}case {label}:\n")
                out.write(f"{indent}{{\n")

            # Single entry, no guard -> simple body
            if len(entries) == 1 and entries[0][0] is None:
                _, body, cap, as_name, raw_names, _loc = entries[0]
                self._emit_switch_binding(out, cap, as_name, raw_names, inner, subject_expr)
                self.ctx.indent_level += 1
                for s in body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 1
            else:
                # Emit capture/as binding before the guard chain so guards
                # can reference the bound variable
                bindings_emitted: set[str] = set()
                for _g, _b, cap, as_name, raw_names, _loc in entries:
                    for escaped, raw in self._binding_pairs(cap, as_name, raw_names):
                        if escaped not in bindings_emitted:
                            if raw in self.ctx.declared_vars:
                                out.write(f"{inner}{escaped} = {subject_expr};\n")
                            else:
                                out.write(f"{inner}auto& {escaped} = {subject_expr};\n")
                            bindings_emitted.add(escaped)

                # Guard chain: if (g1) { body1 } else if (g2) { body2 } else { fallback }
                has_unguarded = any(g is None for g, _, _, _, _, _ in entries)
                if_opened = False
                for _j, (guard, body, _cap, _as, _raw, _loc) in enumerate(entries):
                    if guard is not None:
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, inner)
                        keyword = "if" if not if_opened else "} else if"
                        if_opened = True
                        out.write(f"{inner}{keyword} ({guard_code}) {{\n")
                        self.ctx.indent_level += 2
                        for s in body:
                            self.gen_stmt(out, s)
                        self.ctx.indent_level -= 2
                    else:
                        # Unguarded entry: final else
                        out.write(f"{inner}}} else {{\n")
                        self.ctx.indent_level += 2
                        for s in body:
                            self.gen_stmt(out, s)
                        self.ctx.indent_level -= 2
                # Close last if/else and add goto fallback if needed
                if has_unguarded:
                    out.write(f"{inner}}}\n")
                elif default_label is not None and labels != ["default"]:
                    out.write(f"{inner}}}\n")
                    out.write(f"{inner}goto {default_label};\n")
                else:
                    out.write(f"{inner}}}\n")

            out.write(f"{inner}break;\n")
            out.write(f"{indent}}}\n")

        out.write(f"{indent}}}\n")

    def _emit_switch_binding(
        self, out: TextIO, cap: str | None, as_name: str | None,
        raw_names: set[str], inner: str,
        subject_expr: str = "__match_subject",
    ) -> None:
        """Emit capture/as binding in a switch arm, respecting declared_vars."""
        for escaped, raw in self._binding_pairs(cap, as_name, raw_names):
            if raw in self.ctx.declared_vars:
                out.write(f"{inner}{escaped} = {subject_expr};\n")
            else:
                out.write(f"{inner}auto& {escaped} = {subject_expr};\n")

    @staticmethod
    def _binding_pairs(
        cap: str | None, as_name: str | None, raw_names: set[str],
    ) -> list[tuple[str, str]]:
        """Return (escaped_name, raw_name) pairs for binding emission."""
        pairs: list[tuple[str, str]] = []
        # raw_names may contain 1 or 2 entries; match escaped names to raw
        raw_list = list(raw_names)
        if cap is not None:
            raw = next((r for r in raw_list if escape_cpp_name(r) == cap), cap)
            pairs.append((cap, raw))
        if as_name is not None and as_name != cap:
            raw = next((r for r in raw_list if escape_cpp_name(r) == as_name), as_name)
            pairs.append((as_name, raw))
        return pairs

    def _unwrap_as_pattern(
        self, pattern: TpyPattern,
    ) -> tuple[TpyPattern, str | None, str | None]:
        """Unwrap as-pattern, returning (inner_pattern, escaped_as_name, raw_as_name)."""
        if isinstance(pattern, TpyAsPattern):
            return pattern.pattern, escape_cpp_name(pattern.name), pattern.name
        return pattern, None, None

    def _gen_switch_as_binding(
        self, out: TextIO, as_name: str | None, as_raw_name: str | None, inner: str,
    ) -> None:
        """Emit as-pattern binding in a switch arm (binds to __match_subject)."""
        if as_name is not None:
            if as_raw_name and as_raw_name in self.ctx.declared_vars:
                out.write(f"{inner}{as_name} = __match_subject;\n")
            else:
                out.write(f"{inner}auto& {as_name} = __match_subject;\n")

    def _gen_switch_default_arm(
        self, out: TextIO, pattern: TpyWildcardPattern | TpyCapturePattern,
        as_name: str | None, as_raw_name: str | None,
        body: list[TpyStmt], indent: str, inner: str,
    ) -> None:
        """Emit a default: arm in a switch statement."""
        out.write(f"{indent}default: {{\n")
        if isinstance(pattern, TpyCapturePattern):
            name = escape_cpp_name(pattern.name)
            if pattern.name in self.ctx.declared_vars:
                out.write(f"{inner}{name} = __match_subject;\n")
            else:
                out.write(f"{inner}auto& {name} = __match_subject;\n")
        self._gen_switch_as_binding(out, as_name, as_raw_name, inner)
        self.ctx.indent_level += 1
        for s in body:
            self.gen_stmt(out, s)
        self.ctx.indent_level -= 1
        out.write(f"{inner}break;\n")
        out.write(f"{indent}}}\n")

    def _gen_match_guarded_union(
        self, out: TextIO, stmt: TpyMatch, subject_type: UnionType, indent: str,
    ) -> None:
        """Generate guarded match on union using goto for fallthrough.

        Uses standalone if-blocks per arm with goto to skip remaining arms
        after a match. This avoids do/while which would capture break/continue
        from user code inside match bodies.
        """
        self.ctx.match_counter += 1
        end_label = f"__match_end_{self.ctx.match_counter}"
        inner = INDENT * (self.ctx.indent_level + 1)

        for i, case in enumerate(stmt.cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            pattern, as_name, as_raw_name = self._unwrap_as_pattern(case.pattern)

            if isinstance(pattern, TpyClassPattern):
                self._gen_guarded_union_class_arm(
                    out, pattern, i, indent, inner, case.body,
                    case.guard, as_name, as_raw_name, case.type_facts, end_label,
                )

            elif isinstance(pattern, TpyOrPattern):
                self._gen_guarded_union_or_arm(
                    out, pattern, i, indent, inner, case.body,
                    case.guard, subject_type, case.type_facts, end_label,
                )

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                if isinstance(pattern, TpyCapturePattern):
                    name = escape_cpp_name(pattern.name)
                    if pattern.name in self.ctx.declared_vars:
                        out.write(f"{indent}{name} = __match_subject;\n")
                    else:
                        out.write(f"{indent}auto& {name} = __match_subject;\n")
                if as_name is not None:
                    if as_raw_name and as_raw_name in self.ctx.declared_vars:
                        out.write(f"{indent}{as_name} = __match_subject;\n")
                    else:
                        out.write(f"{indent}auto& {as_name} = __match_subject;\n")
                if case.guard is not None:
                    guard_code = self.expressions.gen_expr(case.guard)
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}if ({guard_code}) {{\n")
                    self.ctx.indent_level += 1
                    for s in case.body:
                        self.gen_stmt(out, s)
                    out.write(f"{INDENT * self.ctx.indent_level}goto {end_label};\n")
                    self.ctx.indent_level -= 1
                    out.write(f"{indent}}}\n")
                else:
                    for s in case.body:
                        self.gen_stmt(out, s)

            else:
                raise CodeGenError(f"Unsupported pattern in guarded union match: {type(pattern).__name__}")

        out.write(f"{end_label}:;\n")

    def _gen_guarded_union_class_arm(
        self, out: TextIO, pattern: TpyClassPattern, arm_idx: int | str,
        indent: str, inner: str, body: list[TpyStmt],
        guard: TpyExpr | None,
        as_name: str | None, as_raw_name: str | None,
        type_facts: dict[str, TpyType] | None,
        end_label: str,
    ) -> None:
        """Generate a class-pattern arm for guarded union match (goto-based fallthrough)."""
        assert pattern.resolved_type is not None
        cpp_type = self.types.type_to_cpp(pattern.resolved_type)

        out.write(f"{indent}if (std::holds_alternative<{cpp_type}>(__match_subject)) {{\n")
        case_var: str | None = None
        if pattern.keywords or type_facts or as_name is not None:
            case_var = f"__case_{arm_idx}"
            out.write(f"{inner}auto& {case_var} = std::get<{cpp_type}>(__match_subject);\n")
        if pattern.keywords:
            self._gen_match_field_bindings(out, pattern, case_var, inner)
        if as_name is not None:
            if as_raw_name and as_raw_name in self.ctx.declared_vars:
                out.write(f"{inner}{as_name} = {case_var};\n")
            else:
                out.write(f"{inner}auto& {as_name} = {case_var};\n")

        if guard is not None:
            guard_code = self.expressions.gen_expr(guard)
            self.ctx.temps.flush(out, inner)
            out.write(f"{inner}if ({guard_code}) {{\n")
            saved_narrow = self._apply_narrowing(type_facts, case_var)
            self.ctx.indent_level += 2
            for s in body:
                self.gen_stmt(out, s)
            out.write(f"{INDENT * self.ctx.indent_level}goto {end_label};\n")
            self.ctx.indent_level -= 2
            self._restore_narrowed_vars(saved_narrow)
            out.write(f"{inner}}}\n")
        else:
            saved_narrow = self._apply_narrowing(type_facts, case_var)
            self.ctx.indent_level += 1
            for s in body:
                self.gen_stmt(out, s)
            out.write(f"{INDENT * self.ctx.indent_level}goto {end_label};\n")
            self.ctx.indent_level -= 1
            self._restore_narrowed_vars(saved_narrow)

        out.write(f"{indent}}}\n")

    def _gen_guarded_union_or_arm(
        self, out: TextIO, pattern: TpyOrPattern, arm_idx: int,
        indent: str, inner: str, body: list[TpyStmt],
        guard: TpyExpr | None, subject_type: UnionType,
        type_facts: dict[str, TpyType] | None,
        end_label: str,
    ) -> None:
        """Generate an or-pattern arm for guarded union match (goto-based fallthrough)."""
        has_bindings = any(
            isinstance(alt, TpyClassPattern) and alt.keywords
            for alt in pattern.patterns
        )

        if not has_bindings:
            conds = []
            for alt in pattern.patterns:
                if isinstance(alt, TpyClassPattern):
                    assert alt.resolved_type is not None
                    cpp_type = self.types.type_to_cpp(alt.resolved_type)
                    conds.append(f"std::holds_alternative<{cpp_type}>(__match_subject)")
                elif isinstance(alt, (TpyWildcardPattern, TpyCapturePattern)):
                    conds.append("true")
                else:
                    raise CodeGenError(f"Unsupported or-pattern alternative: {type(alt).__name__}")
            cond = " || ".join(conds)
            if guard is not None:
                guard_code = self.expressions.gen_expr(guard)
                self.ctx.temps.flush(out, indent)
                cond = f"({cond}) && {guard_code}"
            out.write(f"{indent}if ({cond}) {{\n")
            self.ctx.indent_level += 1
            for s in body:
                self.gen_stmt(out, s)
            out.write(f"{INDENT * self.ctx.indent_level}goto {end_label};\n")
            self.ctx.indent_level -= 1
            out.write(f"{indent}}}\n")
        else:
            for j, alt in enumerate(pattern.patterns):
                if isinstance(alt, TpyClassPattern):
                    self._gen_guarded_union_class_arm(
                        out, alt, f"{arm_idx}_{j}", indent, inner, body,
                        guard, None, None, type_facts, end_label,
                    )
                else:
                    raise CodeGenError(f"Unsupported or-pattern alternative with bindings: {type(alt).__name__}")

    def _apply_narrowing(
        self, type_facts: dict[str, TpyType] | None, case_var: str | None,
    ) -> dict[str, str | None]:
        """Apply narrowing facts and return saved state for later restoration."""
        saved: dict[str, str | None] = {}
        if type_facts:
            for var_name in type_facts:
                saved[var_name] = self.ctx.narrowed_vars.get(var_name)
                self.ctx.narrowed_vars[var_name] = case_var
        return saved

    def _gen_match_if_elif(self, out: TextIO, stmt: TpyMatch, indent: str) -> None:
        """Generate match/case as an if/elif/else chain (for str and float subjects)."""
        inner = INDENT * (self.ctx.indent_level + 1)

        for i, case in enumerate(stmt.cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            keyword = "if" if i == 0 else "} else if"
            pattern = case.pattern
            guard = case.guard

            if isinstance(pattern, TpyLiteralPattern):
                cond = self._gen_match_literal_cond(pattern)
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                self.ctx.indent_level += 1
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, TpyOrPattern):
                conds = []
                for alt in pattern.patterns:
                    if isinstance(alt, TpyLiteralPattern):
                        conds.append(self._gen_match_literal_cond(alt))
                    else:
                        raise CodeGenError(f"Unsupported or-pattern alternative: {type(alt).__name__}")
                cond = " || ".join(conds)
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"({cond}) && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                self.ctx.indent_level += 1
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                # Emit capture binding before the guard check so the guard
                # can reference the captured variable.
                if isinstance(pattern, TpyCapturePattern) and guard is not None:
                    cap_name = escape_cpp_name(pattern.name)
                    if i > 0:
                        out.write(f"{indent}}}\n")
                    if pattern.name in self.ctx.declared_vars:
                        out.write(f"{indent}{cap_name} = __match_subject;\n")
                    else:
                        out.write(f"{indent}auto& {cap_name} = __match_subject;\n")
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}if ({guard_code}) {{\n")
                elif guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}{keyword} ({guard_code}) {{\n")
                elif i == 0:
                    out.write(f"{indent}{{\n")
                else:
                    out.write(f"{indent}}} else {{\n")
                if isinstance(pattern, TpyCapturePattern) and guard is None:
                    cap_name = escape_cpp_name(pattern.name)
                    if pattern.name in self.ctx.declared_vars:
                        out.write(f"{inner}{cap_name} = __match_subject;\n")
                    else:
                        out.write(f"{inner}auto& {cap_name} = __match_subject;\n")
                self.ctx.indent_level += 1
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, TpyAsPattern):
                inner_pat = pattern.pattern
                as_name = escape_cpp_name(pattern.name)
                if isinstance(inner_pat, (TpyLiteralPattern, TpyValuePattern)):
                    if isinstance(inner_pat, TpyLiteralPattern):
                        cond = self._gen_match_literal_cond(inner_pat)
                    else:
                        val_code = self.expressions.gen_expr(inner_pat.expr)
                        self.ctx.temps.flush(out, indent)
                        cond = f"__match_subject == {val_code}"
                    if guard is not None:
                        # Guard may reference as-variable; split into match + bind + guard
                        out.write(f"{indent}{keyword} ({cond}) {{\n")
                        if pattern.name in self.ctx.declared_vars:
                            out.write(f"{inner}{as_name} = __match_subject;\n")
                        else:
                            out.write(f"{inner}auto& {as_name} = __match_subject;\n")
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, inner)
                        out.write(f"{inner}if ({guard_code}) {{\n")
                        self.ctx.indent_level += 2
                        for s in case.body:
                            self.gen_stmt(out, s)
                        self.ctx.indent_level -= 2
                        out.write(f"{inner}}}\n")
                    else:
                        out.write(f"{indent}{keyword} ({cond}) {{\n")
                        if pattern.name in self.ctx.declared_vars:
                            out.write(f"{inner}{as_name} = __match_subject;\n")
                        else:
                            out.write(f"{inner}auto& {as_name} = __match_subject;\n")
                        self.ctx.indent_level += 1
                        for s in case.body:
                            self.gen_stmt(out, s)
                        self.ctx.indent_level -= 1
                elif isinstance(inner_pat, (TpyWildcardPattern, TpyCapturePattern)):
                    if guard is not None:
                        # Emit binding before guard (like capture+guard path)
                        if i > 0:
                            out.write(f"{indent}}}\n")
                        if pattern.name in self.ctx.declared_vars:
                            out.write(f"{indent}{as_name} = __match_subject;\n")
                        else:
                            out.write(f"{indent}auto& {as_name} = __match_subject;\n")
                        if isinstance(inner_pat, TpyCapturePattern):
                            inner_name = escape_cpp_name(inner_pat.name)
                            if inner_pat.name in self.ctx.declared_vars:
                                out.write(f"{indent}{inner_name} = __match_subject;\n")
                            else:
                                out.write(f"{indent}auto& {inner_name} = __match_subject;\n")
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, indent)
                        out.write(f"{indent}if ({guard_code}) {{\n")
                    else:
                        if i == 0:
                            out.write(f"{indent}{{\n")
                        else:
                            out.write(f"{indent}}} else {{\n")
                        if pattern.name in self.ctx.declared_vars:
                            out.write(f"{inner}{as_name} = __match_subject;\n")
                        else:
                            out.write(f"{inner}auto& {as_name} = __match_subject;\n")
                        if isinstance(inner_pat, TpyCapturePattern):
                            inner_name = escape_cpp_name(inner_pat.name)
                            if inner_pat.name in self.ctx.declared_vars:
                                out.write(f"{inner}{inner_name} = __match_subject;\n")
                            else:
                                out.write(f"{inner}auto& {inner_name} = __match_subject;\n")
                    self.ctx.indent_level += 1
                    for s in case.body:
                        self.gen_stmt(out, s)
                    self.ctx.indent_level -= 1
                else:
                    raise CodeGenError(f"Unsupported as-pattern inner: {type(inner_pat).__name__}")

            else:
                raise CodeGenError(f"Unsupported match pattern: {type(pattern).__name__}")

        out.write(f"{indent}}}\n")

    def _should_switch_str(self, stmt: TpyMatch) -> bool:
        """Check if a string match has enough unguarded literal cases for switch dispatch."""
        count = 0
        for case in stmt.cases:
            if case.guard is not None:
                continue
            pat = case.pattern
            if isinstance(pat, TpyAsPattern):
                pat = pat.pattern
            if isinstance(pat, TpyLiteralPattern) and isinstance(pat.value, str):
                count += 1
            elif isinstance(pat, TpyOrPattern):
                if all(isinstance(a, TpyLiteralPattern) and isinstance(a.value, str)
                       for a in pat.patterns):
                    count += len(pat.patterns)
        return count >= STRING_SWITCH_THRESHOLD

    def _gen_match_switch_str(self, out: TextIO, stmt: TpyMatch, indent: str) -> None:
        """Generate optimized switch-based dispatch for string match/case."""
        inner = INDENT * (self.ctx.indent_level + 1)
        deep = INDENT * (self.ctx.indent_level + 2)

        self.ctx.match_counter += 1
        end_label = f"__match_end_{self.ctx.match_counter}"

        # Partition cases into guarded literals, unguarded literals, and trailing
        guarded: list[TpyMatchCase] = []
        # Each unguarded entry: (case, list_of_string_values)
        unguarded: list[tuple[TpyMatchCase, list[str]]] = []
        trailing: list[TpyMatchCase] = []

        for case in stmt.cases:
            pat = case.pattern
            if isinstance(pat, TpyAsPattern):
                pat = pat.pattern
            is_str_lit = isinstance(pat, TpyLiteralPattern) and isinstance(pat.value, str)
            is_str_or = (isinstance(pat, TpyOrPattern) and
                         all(isinstance(a, TpyLiteralPattern) and isinstance(a.value, str)
                             for a in pat.patterns))
            if is_str_lit or is_str_or:
                if case.guard is not None:
                    guarded.append(case)
                else:
                    strs = [pat.value] if is_str_lit else [a.value for a in pat.patterns]
                    unguarded.append((case, strs))
            else:
                trailing.append(case)

        # Collect all strings and find best discriminator
        all_strings = []
        for _, strs in unguarded:
            all_strings.extend(strs)
        kind, param, _buckets = find_best_discriminator(all_strings)

        # Build bucket -> [(case, string_value)] mapping, preserving arm order
        bucket_entries: dict[int, list[tuple[TpyMatchCase, str]]] = defaultdict(list)
        for case, strs in unguarded:
            for s in strs:
                key = len(s) if kind == "length" else ord(s[param])
                bucket_entries[key].append((case, s))

        # Emit guarded string literal arms first (pre-switch, in original order)
        for case in guarded:
            self.ctx.emit_source_comment(out, case.loc, indent)
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
            if isinstance(pattern, TpyLiteralPattern):
                cond = self._gen_match_literal_cond(pattern)
            else:
                # Or-pattern
                conds = [self._gen_match_literal_cond(a) for a in pattern.patterns]
                cond = " || ".join(conds)
            guard_code = self.expressions.gen_expr(case.guard)
            self.ctx.temps.flush(out, indent)
            is_or = isinstance(pattern, TpyOrPattern)
            cond = f"({cond}) && {guard_code}" if is_or else f"{cond} && {guard_code}"
            out.write(f"{indent}if ({cond}) {{\n")
            if as_name is not None:
                if as_raw and as_raw in self.ctx.declared_vars:
                    out.write(f"{inner}{as_name} = __match_subject;\n")
                else:
                    out.write(f"{inner}auto& {as_name} = __match_subject;\n")
            self.ctx.indent_level += 1
            for s in case.body:
                self.gen_stmt(out, s)
            self.ctx.indent_level -= 1
            out.write(f"{inner}goto {end_label};\n")
            out.write(f"{indent}}}\n")

        # Emit switch on discriminator
        # For char_at, wrap in an if-guard so short strings skip the switch
        sw_indent = indent
        sw_inner = inner
        sw_deep = deep
        if kind == "char_at":
            out.write(f"{indent}if (__match_subject.size() >= {param + 1}) {{\n")
            sw_indent = inner
            sw_inner = deep
            sw_deep = INDENT * (self.ctx.indent_level + 3)
            out.write(f"{sw_indent}switch (static_cast<unsigned char>(__match_subject[{param}])) {{\n")
        else:
            out.write(f"{indent}switch (__match_subject.size()) {{\n")

        for disc_value in sorted(bucket_entries.keys()):
            entries = bucket_entries[disc_value]
            if kind == "char_at":
                ch = chr(disc_value)
                out.write(f"{sw_indent}case '{escape_cpp_char(ch)}': {{\n")
            else:
                out.write(f"{sw_indent}case {disc_value}: {{\n")
            for case, string_val in entries:
                self.ctx.emit_source_comment(out, case.loc, sw_inner)
                pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
                out.write(f'{sw_inner}if (__match_subject == "{escape_cpp_string(string_val)}") {{\n')
                if as_name is not None:
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{sw_deep}{as_name} = __match_subject;\n")
                    else:
                        out.write(f"{sw_deep}auto& {as_name} = __match_subject;\n")
                self.ctx.indent_level += (3 if kind == "char_at" else 2)
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= (3 if kind == "char_at" else 2)
                out.write(f"{sw_deep}goto {end_label};\n")
                out.write(f"{sw_inner}}}\n")
            out.write(f"{sw_inner}break;\n")
            out.write(f"{sw_indent}}}\n")

        out.write(f"{sw_indent}}}\n")  # close switch
        if kind == "char_at":
            out.write(f"{indent}}}\n")  # close if-guard

        # Emit trailing arms (wildcard, capture, etc.) in a block scope
        # to prevent goto from crossing variable declarations
        if trailing:
            out.write(f"{indent}{{\n")
            for case in trailing:
                self.ctx.emit_source_comment(out, case.loc, inner)
                pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
                if isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                    if isinstance(pattern, TpyCapturePattern):
                        cap_name = escape_cpp_name(pattern.name)
                        if pattern.name in self.ctx.declared_vars:
                            out.write(f"{inner}{cap_name} = __match_subject;\n")
                        else:
                            out.write(f"{inner}auto& {cap_name} = __match_subject;\n")
                    if as_name is not None:
                        if as_raw and as_raw in self.ctx.declared_vars:
                            out.write(f"{inner}{as_name} = __match_subject;\n")
                        else:
                            out.write(f"{inner}auto& {as_name} = __match_subject;\n")
                    if case.guard is not None:
                        guard_code = self.expressions.gen_expr(case.guard)
                        self.ctx.temps.flush(out, inner)
                        out.write(f"{inner}if ({guard_code}) {{\n")
                        self.ctx.indent_level += 2
                        for s in case.body:
                            self.gen_stmt(out, s)
                        self.ctx.indent_level -= 2
                        out.write(f"{inner}}}\n")
                    else:
                        self.ctx.indent_level += 1
                        for s in case.body:
                            self.gen_stmt(out, s)
                        self.ctx.indent_level -= 1
                else:
                    raise CodeGenError(
                        f"Unsupported trailing pattern in string switch: {type(pattern).__name__}"
                    )
            out.write(f"{indent}}}\n")

        out.write(f"{indent}{end_label}:;\n")

    def _gen_match_if_elif_record(self, out: TextIO, stmt: TpyMatch, indent: str) -> None:
        """Generate match/case as if/elif chain for concrete record subjects (no guards)."""
        inner = INDENT * (self.ctx.indent_level + 1)

        for i, case in enumerate(stmt.cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            keyword = "if" if i == 0 else "} else if"
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)

            if isinstance(pattern, TpyClassPattern):
                conds = self._record_field_conditions(pattern)
                if conds:
                    out.write(f"{indent}{keyword} ({' && '.join(conds)}) {{\n")
                elif i == 0:
                    out.write(f"{indent}{{\n")
                else:
                    out.write(f"{indent}}} else {{\n")
                self._gen_match_field_bindings(out, pattern, "__match_subject", inner)
                if as_name is not None:
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{inner}{as_name} = __match_subject;\n")
                    else:
                        out.write(f"{inner}auto& {as_name} = __match_subject;\n")
                self.ctx.indent_level += 1
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, TpyOrPattern):
                or_parts: list[str] = []
                for alt in pattern.patterns:
                    if isinstance(alt, TpyClassPattern):
                        alt_conds = self._record_field_conditions(alt)
                        if alt_conds:
                            or_parts.append("(" + " && ".join(alt_conds) + ")")
                    elif isinstance(alt, TpyWildcardPattern):
                        or_parts.clear()
                        break
                    else:
                        raise CodeGenError(
                            f"Unsupported or-pattern alternative for record: "
                            f"{type(alt).__name__}"
                        )
                if or_parts:
                    out.write(f"{indent}{keyword} ({' || '.join(or_parts)}) {{\n")
                elif i == 0:
                    out.write(f"{indent}{{\n")
                else:
                    out.write(f"{indent}}} else {{\n")
                self.ctx.indent_level += 1
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                if i == 0:
                    out.write(f"{indent}{{\n")
                else:
                    out.write(f"{indent}}} else {{\n")
                if isinstance(pattern, TpyCapturePattern):
                    cap_name = escape_cpp_name(pattern.name)
                    if pattern.name in self.ctx.declared_vars:
                        out.write(f"{inner}{cap_name} = __match_subject;\n")
                    else:
                        out.write(f"{inner}auto& {cap_name} = __match_subject;\n")
                if as_name is not None:
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{inner}{as_name} = __match_subject;\n")
                    else:
                        out.write(f"{inner}auto& {as_name} = __match_subject;\n")
                self.ctx.indent_level += 1
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 1

            else:
                raise CodeGenError(f"Unsupported match pattern for record: {type(pattern).__name__}")

        out.write(f"{indent}}}\n")

    def _gen_match_guarded_record(self, out: TextIO, stmt: TpyMatch, indent: str) -> None:
        """Generate match/case for record subjects with guards using standalone ifs + goto."""
        inner = INDENT * (self.ctx.indent_level + 1)
        self.ctx.match_counter += 1
        end_label = f"__match_end_{self.ctx.match_counter}"

        for i, case in enumerate(stmt.cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
            guard = case.guard

            if isinstance(pattern, TpyClassPattern):
                conds = self._record_field_conditions(pattern)
                if conds:
                    out.write(f"{indent}if ({' && '.join(conds)}) {{\n")
                else:
                    out.write(f"{indent}{{\n")
                self._gen_match_field_bindings(out, pattern, "__match_subject", inner)
                if as_name is not None:
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{inner}{as_name} = __match_subject;\n")
                    else:
                        out.write(f"{inner}auto& {as_name} = __match_subject;\n")
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, inner)
                    out.write(f"{inner}if ({guard_code}) {{\n")
                    self.ctx.indent_level += 2
                    for s in case.body:
                        self.gen_stmt(out, s)
                    self.ctx.indent_level -= 2
                    out.write(f"{inner}    goto {end_label};\n")
                    out.write(f"{inner}}}\n")
                else:
                    self.ctx.indent_level += 1
                    for s in case.body:
                        self.gen_stmt(out, s)
                    self.ctx.indent_level -= 1
                    out.write(f"{inner}goto {end_label};\n")
                out.write(f"{indent}}}\n")

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                if isinstance(pattern, TpyCapturePattern):
                    cap_name = escape_cpp_name(pattern.name)
                    if pattern.name in self.ctx.declared_vars:
                        out.write(f"{indent}{cap_name} = __match_subject;\n")
                    else:
                        out.write(f"{indent}auto& {cap_name} = __match_subject;\n")
                if as_name is not None:
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{indent}{as_name} = __match_subject;\n")
                    else:
                        out.write(f"{indent}auto& {as_name} = __match_subject;\n")
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}if ({guard_code}) {{\n")
                    self.ctx.indent_level += 1
                    for s in case.body:
                        self.gen_stmt(out, s)
                    self.ctx.indent_level -= 1
                    out.write(f"{inner}goto {end_label};\n")
                    out.write(f"{indent}}}\n")
                else:
                    out.write(f"{indent}{{\n")
                    self.ctx.indent_level += 1
                    for s in case.body:
                        self.gen_stmt(out, s)
                    self.ctx.indent_level -= 1
                    out.write(f"{indent}}}\n")

            elif isinstance(pattern, TpyOrPattern):
                or_parts: list[str] = []
                for alt in pattern.patterns:
                    if isinstance(alt, TpyClassPattern):
                        alt_conds = self._record_field_conditions(alt)
                        if alt_conds:
                            or_parts.append("(" + " && ".join(alt_conds) + ")")
                    elif isinstance(alt, TpyWildcardPattern):
                        or_parts.clear()
                        break
                    else:
                        raise CodeGenError(
                            f"Unsupported or-pattern alternative for record: "
                            f"{type(alt).__name__}"
                        )
                if or_parts:
                    cond = " || ".join(or_parts)
                    if guard is not None:
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, indent)
                        cond = f"({cond}) && {guard_code}"
                    out.write(f"{indent}if ({cond}) {{\n")
                else:
                    if guard is not None:
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, indent)
                        out.write(f"{indent}if ({guard_code}) {{\n")
                    else:
                        out.write(f"{indent}{{\n")
                self.ctx.indent_level += 1
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 1
                out.write(f"{inner}goto {end_label};\n")
                out.write(f"{indent}}}\n")

            else:
                raise CodeGenError(f"Unsupported match pattern for record: {type(pattern).__name__}")

        out.write(f"{indent}{end_label}:;\n")

    def _record_field_conditions(self, pattern: 'TpyClassPattern') -> list[str]:
        """Generate C++ field comparison conditions for a record class pattern."""
        conds: list[str] = []
        for field_name, sub_pattern in pattern.keywords:
            if isinstance(sub_pattern, TpyLiteralPattern):
                val = sub_pattern.value
                if isinstance(val, bool):
                    conds.append(f"__match_subject.{field_name} == {'true' if val else 'false'}")
                elif isinstance(val, int):
                    conds.append(f"__match_subject.{field_name} == {val}")
                elif isinstance(val, float):
                    conds.append(f"__match_subject.{field_name} == {val!r}")
                elif isinstance(val, str):
                    conds.append(f'__match_subject.{field_name} == "{escape_cpp_string(val)}"')
        return conds

    # ------------------------------------------------------------------
    # Optimized Optional match: hoist null check, dispatch inner
    # ------------------------------------------------------------------

    def _partition_optional_cases(
        self, cases: list['TpyMatchCase'],
    ) -> tuple[list['TpyMatchCase'], list['TpyMatchCase']] | None:
        """Split cases into (none_cases, inner_cases) if None arms form a prefix.

        Returns None if the optimization cannot be applied:
        - None arms don't form a contiguous prefix
        - An or-pattern mixes None and non-None alternatives
        - A None arm has a guard (guard failure needs fallthrough to later arms)
        """
        none_cases: list[TpyMatchCase] = []
        inner_cases: list[TpyMatchCase] = []
        seen_inner = False

        for case in cases:
            pat = case.pattern
            if isinstance(pat, TpyAsPattern):
                pat = pat.pattern

            # Or-pattern mixing None and non-None -- bail out
            if isinstance(pat, TpyOrPattern):
                has_none = any(
                    isinstance(a, TpyLiteralPattern) and a.value is None
                    for a in pat.patterns
                )
                has_other = any(
                    not (isinstance(a, TpyLiteralPattern) and a.value is None)
                    for a in pat.patterns
                )
                if has_none and has_other:
                    return None
                if has_none:
                    if seen_inner:
                        return None
                    if case.guard is not None:
                        return None
                    none_cases.append(case)
                else:
                    seen_inner = True
                    inner_cases.append(case)
                continue

            is_none = isinstance(pat, TpyLiteralPattern) and pat.value is None
            if is_none:
                if seen_inner:
                    return None
                # Guarded None arm needs fallthrough to later arms on guard failure
                if case.guard is not None:
                    return None
                none_cases.append(case)
            else:
                seen_inner = True
                inner_cases.append(case)

        if not inner_cases:
            return None
        return none_cases, inner_cases

    def _gen_match_optimized_optional(
        self, out: TextIO, stmt: TpyMatch, subject_type: OptionalType,
        none_cases: list['TpyMatchCase'], inner_cases: list['TpyMatchCase'],
        indent: str,
    ) -> None:
        """Generate optimized Optional match: if (null) { ... } else { dispatch }."""
        inner = INDENT * (self.ctx.indent_level + 1)
        uses_ptr = subject_type.uses_pointer_repr()
        null_cond = "__match_subject == nullptr" if uses_ptr else "!__match_subject.has_value()"

        # --- None branch ---
        has_value_cond = "__match_subject != nullptr" if uses_ptr else "__match_subject.has_value()"
        if not none_cases:
            # No None arms -- just guard on has_value, no else
            out.write(f"{indent}if ({has_value_cond}) {{\n")
        elif len(none_cases) == 1 and none_cases[0].guard is None:
            # Simple: single unguarded None arm
            case = none_cases[0]
            self.ctx.emit_source_comment(out, case.loc, indent)
            out.write(f"{indent}if ({null_cond}) {{\n")
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
            if as_name is not None:
                if as_raw and as_raw in self.ctx.declared_vars:
                    out.write(f"{inner}{as_name} = __match_subject;\n")
                else:
                    out.write(f"{inner}auto& {as_name} = __match_subject;\n")
            self.ctx.indent_level += 1
            for s in case.body:
                self.gen_stmt(out, s)
            self.ctx.indent_level -= 1
        else:
            # Multiple or guarded None arms: guard chain inside null block
            self.ctx.emit_source_comment(out, none_cases[0].loc, indent)
            out.write(f"{indent}if ({null_cond}) {{\n")
            for j, case in enumerate(none_cases):
                if j > 0:
                    self.ctx.emit_source_comment(out, case.loc, inner)
                pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
                if case.guard is not None:
                    guard_code = self.expressions.gen_expr(case.guard)
                    self.ctx.temps.flush(out, inner)
                    kw = "if" if j == 0 else "} else if"
                    out.write(f"{inner}{kw} ({guard_code}) {{\n")
                else:
                    if j == 0:
                        pass  # body goes directly in null block
                    else:
                        out.write(f"{inner}}} else {{\n")
                if as_name is not None:
                    deep = INDENT * (self.ctx.indent_level + 2) if case.guard is not None or j > 0 else inner
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{deep}{as_name} = __match_subject;\n")
                    else:
                        out.write(f"{deep}auto& {as_name} = __match_subject;\n")
                extra = 2 if case.guard is not None or j > 0 else 1
                self.ctx.indent_level += extra
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= extra
            if any(c.guard is not None for c in none_cases):
                out.write(f"{inner}}}\n")

        # --- Inner value dispatch ---
        if none_cases:
            out.write(f"{indent}}} else {{\n")
        deref = "(*__match_subject)"
        out.write(f"{inner}auto& __match_inner = {deref};\n")

        inner_type = subject_type.inner
        if isinstance(inner_type, EnumType):
            groups = self._group_switch_arms(inner_cases, kind="enum")
            self.ctx.indent_level += 1
            self._emit_switch_groups(out, groups, inner, subject_expr="__match_inner")
            self.ctx.indent_level -= 1
        elif isinstance(inner_type, (FixedIntType, BoolType)):
            groups = self._group_switch_arms(inner_cases, kind="primitive")
            self.ctx.indent_level += 1
            self._emit_switch_groups(out, groups, inner, subject_expr="__match_inner")
            self.ctx.indent_level -= 1
        elif isinstance(inner_type, NamedType) and inner_type.is_user_record:
            self._emit_optional_inner_record(out, inner_cases, inner)
        else:
            # str, float, other: if/elif chain on __match_inner
            self._emit_optional_inner_if_elif(out, inner_cases, inner)

        out.write(f"{indent}}}\n")

    def _emit_optional_inner_record(
        self, out: TextIO, cases: list['TpyMatchCase'], indent: str,
    ) -> None:
        """Emit if/elif chain for record patterns on dereferenced Optional."""
        inner = INDENT * (self.ctx.indent_level + 2)
        subject_expr = "__match_inner"

        for i, case in enumerate(cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            keyword = "if" if i == 0 else "} else if"
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
            guard = case.guard

            if isinstance(pattern, TpyClassPattern):
                field_conds = self._record_field_conditions_on(pattern, subject_expr)
                cond = " && ".join(field_conds) if field_conds else "true"
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}" if field_conds else guard_code
                if not field_conds and guard is None:
                    # Always-matching class pattern -> else
                    if i == 0:
                        out.write(f"{indent}{{\n")
                    else:
                        out.write(f"{indent}}} else {{\n")
                else:
                    out.write(f"{indent}{keyword} ({cond}) {{\n")
                # Emit field bindings
                for field_name, sub in pattern.keywords:
                    if isinstance(sub, TpyCapturePattern):
                        name = escape_cpp_name(sub.name)
                        acc = f"{subject_expr}.{field_name}"
                        if sub.name in self.ctx.declared_vars:
                            out.write(f"{inner}{name} = {acc};\n")
                        else:
                            out.write(f"{inner}auto& {name} = {acc};\n")
                if as_name is not None:
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{inner}{as_name} = {subject_expr};\n")
                    else:
                        out.write(f"{inner}auto& {as_name} = {subject_expr};\n")
                self.ctx.indent_level += 2
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 2

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                if i == 0:
                    out.write(f"{indent}{{\n")
                else:
                    out.write(f"{indent}}} else {{\n")
                if isinstance(pattern, TpyCapturePattern):
                    cap_name = escape_cpp_name(pattern.name)
                    if pattern.name in self.ctx.declared_vars:
                        out.write(f"{inner}{cap_name} = {subject_expr};\n")
                    else:
                        out.write(f"{inner}auto& {cap_name} = {subject_expr};\n")
                if as_name is not None:
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{inner}{as_name} = {subject_expr};\n")
                    else:
                        out.write(f"{inner}auto& {as_name} = {subject_expr};\n")
                self.ctx.indent_level += 2
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 2

            elif isinstance(pattern, TpyOrPattern):
                or_conds: list[str] = []
                for alt in pattern.patterns:
                    if isinstance(alt, TpyClassPattern):
                        fc = self._record_field_conditions_on(alt, subject_expr)
                        or_conds.append("(" + " && ".join(fc) + ")" if fc else "true")
                    elif isinstance(alt, (TpyWildcardPattern, TpyCapturePattern)):
                        or_conds.clear()
                        break
                    else:
                        raise CodeGenError(
                            f"Unsupported or-pattern alt in Optional record: {type(alt).__name__}"
                        )
                if or_conds:
                    cond = " || ".join(or_conds)
                    if guard is not None:
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, indent)
                        cond = f"({cond}) && {guard_code}"
                    out.write(f"{indent}{keyword} ({cond}) {{\n")
                else:
                    if i == 0:
                        out.write(f"{indent}{{\n")
                    else:
                        out.write(f"{indent}}} else {{\n")
                self.ctx.indent_level += 2
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 2

            else:
                raise CodeGenError(
                    f"Unsupported pattern in Optional record dispatch: {type(pattern).__name__}"
                )

        out.write(f"{indent}}}\n")

    def _emit_optional_inner_if_elif(
        self, out: TextIO, cases: list['TpyMatchCase'], indent: str,
    ) -> None:
        """Emit if/elif chain for literal/value patterns on dereferenced Optional."""
        inner = INDENT * (self.ctx.indent_level + 2)
        deref = "__match_inner"

        for i, case in enumerate(cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            keyword = "if" if i == 0 else "} else if"
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
            guard = case.guard

            if isinstance(pattern, TpyLiteralPattern):
                cond = self._gen_literal_cond_on(pattern, deref)
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                if as_name is not None:
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{inner}{as_name} = {deref};\n")
                    else:
                        out.write(f"{inner}auto& {as_name} = {deref};\n")
                self.ctx.indent_level += 2
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 2

            elif isinstance(pattern, TpyValuePattern):
                val_code = self.expressions.gen_expr(pattern.expr)
                self.ctx.temps.flush(out, indent)
                cond = f"{deref} == {val_code}"
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                if as_name is not None:
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{inner}{as_name} = {deref};\n")
                    else:
                        out.write(f"{inner}auto& {as_name} = {deref};\n")
                self.ctx.indent_level += 2
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 2

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                if i == 0:
                    out.write(f"{indent}{{\n")
                else:
                    out.write(f"{indent}}} else {{\n")
                if isinstance(pattern, TpyCapturePattern):
                    cap_name = escape_cpp_name(pattern.name)
                    if pattern.name in self.ctx.declared_vars:
                        out.write(f"{inner}{cap_name} = {deref};\n")
                    else:
                        out.write(f"{inner}auto& {cap_name} = {deref};\n")
                if as_name is not None:
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{inner}{as_name} = {deref};\n")
                    else:
                        out.write(f"{inner}auto& {as_name} = {deref};\n")
                self.ctx.indent_level += 2
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 2

            elif isinstance(pattern, TpyOrPattern):
                or_conds: list[str] = []
                for alt in pattern.patterns:
                    if isinstance(alt, TpyLiteralPattern):
                        or_conds.append(self._gen_literal_cond_on(alt, deref))
                    elif isinstance(alt, TpyValuePattern):
                        val_code = self.expressions.gen_expr(alt.expr)
                        self.ctx.temps.flush(out, indent)
                        or_conds.append(f"{deref} == {val_code}")
                    elif isinstance(alt, (TpyWildcardPattern, TpyCapturePattern)):
                        or_conds.clear()
                        break
                    else:
                        raise CodeGenError(
                            f"Unsupported or-pattern alt in Optional inner: {type(alt).__name__}"
                        )
                if or_conds:
                    cond = " || ".join(or_conds)
                    if guard is not None:
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, indent)
                        cond = f"({cond}) && {guard_code}"
                    out.write(f"{indent}{keyword} ({cond}) {{\n")
                else:
                    if i == 0:
                        out.write(f"{indent}{{\n")
                    else:
                        out.write(f"{indent}}} else {{\n")
                self.ctx.indent_level += 2
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 2

            else:
                raise CodeGenError(
                    f"Unsupported pattern in Optional inner dispatch: {type(pattern).__name__}"
                )

        out.write(f"{indent}}}\n")

    def _gen_literal_cond_on(self, pattern: 'TpyLiteralPattern', subject_expr: str) -> str:
        """Generate condition for literal match on a given subject expression."""
        val = pattern.value
        if isinstance(val, bool):
            return f"{subject_expr} == {'true' if val else 'false'}"
        elif isinstance(val, int):
            return f"{subject_expr} == {val}"
        elif isinstance(val, float):
            return f"{subject_expr} == {val!r}"
        elif isinstance(val, str):
            return f'{subject_expr} == "{escape_cpp_string(val)}"'
        else:
            raise CodeGenError(f"Unsupported literal in match: {val!r}")

    def _gen_match_if_elif_optional(
        self, out: TextIO, stmt: TpyMatch, subject_type: OptionalType, indent: str,
    ) -> None:
        """Generate match/case as if/elif chain for Optional subjects."""
        inner = INDENT * (self.ctx.indent_level + 1)
        uses_ptr = subject_type.uses_pointer_repr()
        # Determine how to check null and dereference
        null_cond = "__match_subject == nullptr" if uses_ptr else "!__match_subject.has_value()"
        has_val_cond = "__match_subject != nullptr" if uses_ptr else "__match_subject.has_value()"
        deref = "(*__match_subject)"

        for i, case in enumerate(stmt.cases):
            self.ctx.emit_source_comment(out, case.loc, indent)
            keyword = "if" if i == 0 else "} else if"
            pattern, as_name, as_raw = self._unwrap_as_pattern(case.pattern)
            guard = case.guard

            if isinstance(pattern, TpyLiteralPattern) and pattern.value is None:
                # case None:
                cond = null_cond
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                if as_name is not None:
                    # as-binding for None case binds the whole optional
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{inner}{as_name} = __match_subject;\n")
                    else:
                        out.write(f"{inner}auto& {as_name} = __match_subject;\n")
                self.ctx.indent_level += 1
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, TpyLiteralPattern):
                # Literal match on inner value (e.g. case 42: on Optional[Int32])
                lit_cond = self._gen_literal_cond_on(pattern, deref)
                cond = f"{has_val_cond} && {lit_cond}"
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                if as_name is not None:
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{inner}{as_name} = {deref};\n")
                    else:
                        out.write(f"{inner}auto& {as_name} = {deref};\n")
                self.ctx.indent_level += 1
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, TpyValuePattern):
                # Value pattern on inner type (e.g. case Color.RED: on Optional[Color])
                val_code = self.expressions.gen_expr(pattern.expr)
                self.ctx.temps.flush(out, indent)
                cond = f"{has_val_cond} && {deref} == {val_code}"
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                if as_name is not None:
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{inner}{as_name} = {deref};\n")
                    else:
                        out.write(f"{inner}auto& {as_name} = {deref};\n")
                self.ctx.indent_level += 1
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, TpyClassPattern):
                # Class pattern on inner record type (e.g. case Point(x=0): on Optional[Point])
                field_conds = self._record_field_conditions_on(pattern, deref)
                cond_parts = [has_val_cond] + field_conds
                cond = " && ".join(cond_parts)
                if guard is not None:
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    cond = f"{cond} && {guard_code}"
                out.write(f"{indent}{keyword} ({cond}) {{\n")
                # Emit field bindings using dereferenced subject
                val_var = f"{deref}"
                for field_name, sub in pattern.keywords:
                    if isinstance(sub, TpyCapturePattern):
                        name = escape_cpp_name(sub.name)
                        acc = f"{val_var}.{field_name}"
                        if sub.name in self.ctx.declared_vars:
                            out.write(f"{inner}{name} = {acc};\n")
                        else:
                            out.write(f"{inner}auto& {name} = {acc};\n")
                if as_name is not None:
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{inner}{as_name} = {deref};\n")
                    else:
                        out.write(f"{inner}auto& {as_name} = {deref};\n")
                self.ctx.indent_level += 1
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, (TpyWildcardPattern, TpyCapturePattern)):
                needs_deref = (
                    isinstance(pattern, TpyCapturePattern) or as_name is not None
                )
                if isinstance(pattern, TpyCapturePattern) and guard is not None:
                    # Guarded capture: condition on has_value + guard
                    cond = f"{has_val_cond} && {self.expressions.gen_expr(guard)}"
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}{keyword} ({cond}) {{\n")
                elif guard is not None:
                    # Guarded wildcard (no capture)
                    guard_code = self.expressions.gen_expr(guard)
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}{keyword} ({guard_code}) {{\n")
                elif needs_deref:
                    # Unguarded capture: guard on has_value to avoid UB
                    out.write(f"{indent}{keyword} ({has_val_cond}) {{\n")
                elif i == 0:
                    out.write(f"{indent}{{\n")
                else:
                    out.write(f"{indent}}} else {{\n")
                if isinstance(pattern, TpyCapturePattern):
                    cap_name = escape_cpp_name(pattern.name)
                    if pattern.name in self.ctx.declared_vars:
                        out.write(f"{inner}{cap_name} = {deref};\n")
                    else:
                        out.write(f"{inner}auto& {cap_name} = {deref};\n")
                if as_name is not None:
                    if as_raw and as_raw in self.ctx.declared_vars:
                        out.write(f"{inner}{as_name} = {deref};\n")
                    else:
                        out.write(f"{inner}auto& {as_name} = {deref};\n")
                self.ctx.indent_level += 1
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 1

            elif isinstance(pattern, TpyOrPattern):
                # OR of None/literal/value/class conditions
                or_conds: list[str] = []
                for alt in pattern.patterns:
                    if isinstance(alt, TpyLiteralPattern) and alt.value is None:
                        or_conds.append(null_cond)
                    elif isinstance(alt, TpyLiteralPattern):
                        lit_c = self._gen_literal_cond_on(alt, deref)
                        or_conds.append(f"({has_val_cond} && {lit_c})")
                    elif isinstance(alt, TpyValuePattern):
                        val_code = self.expressions.gen_expr(alt.expr)
                        self.ctx.temps.flush(out, indent)
                        or_conds.append(f"({has_val_cond} && {deref} == {val_code})")
                    elif isinstance(alt, TpyClassPattern):
                        fc = self._record_field_conditions_on(alt, deref)
                        parts = [has_val_cond] + fc
                        or_conds.append("(" + " && ".join(parts) + ")")
                    elif isinstance(alt, TpyWildcardPattern):
                        or_conds.clear()
                        break
                    else:
                        raise CodeGenError(
                            f"Unsupported or-pattern alt for Optional: {type(alt).__name__}"
                        )
                if or_conds:
                    cond = " || ".join(or_conds)
                    if guard is not None:
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, indent)
                        cond = f"({cond}) && {guard_code}"
                    out.write(f"{indent}{keyword} ({cond}) {{\n")
                else:
                    if guard is not None:
                        guard_code = self.expressions.gen_expr(guard)
                        self.ctx.temps.flush(out, indent)
                        out.write(f"{indent}{keyword} ({guard_code}) {{\n")
                    elif i == 0:
                        out.write(f"{indent}{{\n")
                    else:
                        out.write(f"{indent}}} else {{\n")
                self.ctx.indent_level += 1
                for s in case.body:
                    self.gen_stmt(out, s)
                self.ctx.indent_level -= 1

            else:
                raise CodeGenError(
                    f"Unsupported match pattern for Optional: {type(pattern).__name__}"
                )

        out.write(f"{indent}}}\n")

    def _record_field_conditions_on(
        self, pattern: 'TpyClassPattern', subject_expr: str,
    ) -> list[str]:
        """Generate field conditions using a custom subject expression (e.g. dereferenced pointer)."""
        conds: list[str] = []
        for field_name, sub_pattern in pattern.keywords:
            if isinstance(sub_pattern, TpyLiteralPattern):
                val = sub_pattern.value
                if isinstance(val, bool):
                    conds.append(f"{subject_expr}.{field_name} == {'true' if val else 'false'}")
                elif isinstance(val, int):
                    conds.append(f"{subject_expr}.{field_name} == {val}")
                elif isinstance(val, float):
                    conds.append(f"{subject_expr}.{field_name} == {val!r}")
                elif isinstance(val, str):
                    conds.append(f'{subject_expr}.{field_name} == "{escape_cpp_string(val)}"')
        return conds

    def _variant_index(self, union_type: UnionType, member_type: TpyType) -> int:
        """Find the index of a member type in a union's canonical member ordering."""
        for i, m in enumerate(union_type.members):
            if m == member_type:
                return i
        raise CodeGenError(f"type '{member_type}' not found in union '{union_type}'")

    def _switch_literal_label(self, pattern: TpyLiteralPattern) -> str:
        """Generate a C++ case label for a literal pattern (int or bool)."""
        val = pattern.value
        if isinstance(val, bool):
            return "true" if val else "false"
        elif isinstance(val, int):
            return str(val)
        else:
            raise CodeGenError(f"Cannot use literal {val!r} in switch case label")

    def _gen_match_field_bindings(
        self, out: TextIO, pattern: TpyClassPattern, case_var: str, indent: str,
    ) -> None:
        """Emit local variable bindings for class pattern keyword fields."""
        for field_name, sub_pattern in pattern.keywords:
            if isinstance(sub_pattern, TpyCapturePattern):
                name = escape_cpp_name(sub_pattern.name)
                if sub_pattern.name in self.ctx.declared_vars:
                    # Pre-declared (leaks out of match) -- assign, don't redeclare
                    out.write(f"{indent}{name} = {case_var}.{field_name};\n")
                else:
                    out.write(f"{indent}auto& {name} = {case_var}.{field_name};\n")
            elif isinstance(sub_pattern, TpyWildcardPattern):
                pass
            elif isinstance(sub_pattern, TpyLiteralPattern):
                pass  # Literal sub-patterns handled as conditions (future)

    def _gen_match_literal_cond(self, pattern: TpyLiteralPattern) -> str:
        """Generate a C++ comparison condition for a literal pattern."""
        val = pattern.value
        if isinstance(val, bool):
            return f"__match_subject == {'true' if val else 'false'}"
        elif isinstance(val, int):
            return f"__match_subject == {val}"
        elif isinstance(val, float):
            return f"__match_subject == {val!r}"
        elif isinstance(val, str):
            return f'__match_subject == "{escape_cpp_string(val)}"'
        else:
            raise CodeGenError(f"Unsupported literal pattern value: {val!r}")

    def _gen_if(self, out: TextIO, stmt: TpyIf, indent: str) -> None:
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

        # Pre-declare variables first declared inside branches (all levels).
        # Inner elif branch_decls are typically subsets of the outer's and
        # get skipped by the declared_vars check, but we emit them all for
        # correctness.
        for node in chain:
            self._emit_branch_decls(out, node, indent)

        # Emit if / else if / else chain
        for i, node in enumerate(chain):
            # Nullable protocol param narrowing: replace runtime `x != nullptr`
            # with compile-time `if constexpr (!std::same_as<T_x, nullptr_t>)`.
            # The pointer is guaranteed non-null for real types (call site passes &expr),
            # so the constexpr check alone is sufficient and avoids a redundant branch.
            constexpr_guards = self._get_nullproto_constexpr_guards(node.condition)

            if constexpr_guards:
                # Replace the runtime condition with if constexpr
                guard_conds = " && ".join(
                    f"!std::same_as<T_{gvar}, std::nullptr_t>" for gvar in constexpr_guards
                )
                if i == 0:
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}if constexpr ({guard_conds}) {{\n")
                elif not self.ctx.temps._pending:
                    out.write(f"{indent}}} else if constexpr ({guard_conds}) {{\n")
                else:
                    self.ctx.temps._pending.clear()
                    out.write(f"{indent}}} else {{\n")
                    self.ctx.indent_level += 1
                    self._gen_if(out, node, self.ctx.indent())
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
                elif not self.ctx.temps._pending:
                    else_kw = "else if constexpr" if is_constexpr else "else if"
                    out.write(f"{indent}}} {else_kw} ({cond}) {{\n")
                else:
                    # Elif condition produced temp vars -- can't use flat
                    # else-if (no statements allowed between } and else).
                    # Discard the orphan temps and let _gen_if regenerate
                    # the condition in the correct nested scope.
                    self.ctx.temps._pending.clear()
                    out.write(f"{indent}}} else {{\n")
                    self.ctx.indent_level += 1
                    self._gen_if(out, node, self.ctx.indent())
                    self.ctx.indent_level -= 1
                    out.write(f"{indent}}}\n")
                    return

            then_saved = self._emit_isinstance_extractions(out, node.then_type_facts)

            self.ctx.indent_level += 1
            for s in node.then_body:
                self.gen_stmt(out, s)
            self.ctx.emit_block_trailing_comments(out, node.then_body, self.ctx.indent())
            self.ctx.indent_level -= 1

            self._restore_narrowed_vars(then_saved)

        # Final else branch (from the last node in the chain)
        last = chain[-1]
        if last.else_body:
            out.write(f"{indent}}} else {{\n")
            # Skip else_type_facts extraction when the else body is an elif
            # that will do its own isinstance checks against the original variant.
            is_elif_continuation = (
                len(last.else_body) == 1
                and isinstance(last.else_body[0], TpyIf)
                and self._is_elif(last, last.else_body[0])
            )
            if is_elif_continuation:
                else_saved: dict[str, str | None] = {}
            else:
                else_saved = self._emit_isinstance_extractions(out, last.else_type_facts)

            self.ctx.indent_level += 1
            for s in last.else_body:
                self.gen_stmt(out, s)
            self.ctx.emit_block_trailing_comments(out, last.else_body, self.ctx.indent())
            self.ctx.indent_level -= 1

            self._restore_narrowed_vars(else_saved)

        out.write(f"{indent}}}\n")

    @staticmethod
    def _is_elif(outer: TpyIf, inner: TpyIf) -> bool:
        """True when inner is an elif of outer (not a nested else: if).

        Python's AST represents both as orelse=[If(...)]. We distinguish
        them by column: elif keeps the same column, nested else: if is
        indented deeper.
        """
        if outer.loc is None or inner.loc is None:
            return False
        return inner.loc.column == outer.loc.column

    def _has_concrete_isinstance_facts(self, type_facts: dict[str, TpyType]) -> bool:
        """Check if type_facts contain any concrete types that would emit extractions."""
        return any(
            not isinstance(ty, (UnionType, NoneType)) and not is_protocol_type(ty)
            for ty in type_facts.values()
        )

    def _emit_branch_decls(self, out: TextIO, stmt: TpyStmt, indent: str) -> None:
        """Pre-declare variables first declared inside if/elif/match branches."""
        branch_decls = self.ctx.analyzer.if_branch_decls.get(id(stmt), {})
        for name, var_type in branch_decls.items():
            if (name not in self.ctx.declared_vars
                    and name not in self.ctx.global_declared_vars
                    and name not in self.ctx.native_global_names):
                # @dynamic protocol branch-declared vars: just pre-declare Base* pointer.
                # Per-assignment slots are created by rebind (hoisted to function scope).
                if self._is_dynamic_protocol_type(var_type):
                    base_type = self.protocols.get_dynamic_base_name(var_type.name)
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
                self.ctx.local_scope_names.add(name)
                self.ctx.var_types[name] = var_type
                if self.ctx.current_ns and var_type:
                    self.ctx.current_ns.bind_variable(name, var_type)
                if self._needs_indirection(var_type, name, None):
                    self.ctx.pointer_locals.add(name)
                    if is_const:
                        self.ctx.const_indirect_locals.add(name)
                    if local_var_is_movable(
                            name,
                            self.ctx.hoisted_vars,
                            self.ctx.reassigned_vars,
                            self.ctx.lvalue_reassigned_vars,
                            True):  # branch-declared vars have no init; movability is reassignment-based
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

    def _gen_while(self, out: TextIO, stmt: TpyWhile, indent: str) -> None:
        """Generate a while loop."""
        cond = self.expressions.gen_truthy_expr(stmt.condition)
        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}while ({cond}) {{\n")

        saved = self._emit_isinstance_extractions(out, stmt.then_type_facts)

        self.ctx.indent_level += 1
        for s in stmt.body:
            self.gen_stmt(out, s)
        self.ctx.emit_block_trailing_comments(out, stmt.body, self.ctx.indent())
        self.ctx.indent_level -= 1

        self._restore_narrowed_vars(saved)
        out.write(f"{indent}}}\n")

    def _gen_loop_body(self, out: TextIO, stmt: TpyForEach, indent: str,
                        elem_type: TpyType | None) -> None:
        """Generate loop body statements with namespace/scope tracking.

        Shared by _gen_begin_end_loop, _gen_range_counter_loop, and _gen_for_each.
        Writes the body statements, the closing brace, and cleans up the loop
        variable from var_types.
        """
        self.ctx.local_scope_names.add(stmt.var)
        self.ctx.declared_vars.add(stmt.var)
        if elem_type:
            self.ctx.var_types[stmt.var] = elem_type
        old_ns = self.ctx.current_ns
        if self.ctx.current_ns and elem_type:
            inner_ns = Namespace(parent=self.ctx.current_ns)
            inner_ns.bind_variable(stmt.var, elem_type)
            self.ctx.current_ns = inner_ns
        self.ctx.indent_level += 1
        for s in stmt.body:
            self.gen_stmt(out, s)
        self.ctx.emit_block_trailing_comments(out, stmt.body, self.ctx.indent())
        self.ctx.indent_level -= 1
        self.ctx.local_scope_names.discard(stmt.var)
        self.ctx.current_ns = old_ns

        out.write(f"{indent}}}\n")

        if stmt.var in self.ctx.var_types:
            del self.ctx.var_types[stmt.var]

    def _gen_begin_end_loop(self, out: TextIO, stmt: TpyForEach, indent: str,
                            iterable_expr: str, elem_type: TpyType,
                            is_lvalue: bool | None = None) -> None:
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
        if stmt.const_loop_var and elem_type.is_value_type():
            cpp_elem = elem_type.to_cpp()
            out.write(f"{inner_indent}const {cpp_elem}& {cpp_var} = *{beg_name};\n")
        elif stmt.const_loop_var:
            out.write(f"{inner_indent}const auto& {cpp_var} = *{beg_name};\n")
        elif elem_type.is_value_type():
            cpp_elem = elem_type.to_cpp()
            out.write(f"{inner_indent}{cpp_elem} {cpp_var} = *{beg_name};\n")
        else:
            out.write(f"{inner_indent}auto&& {cpp_var} = *{beg_name};\n")

        self._gen_loop_body(out, stmt, indent, elem_type)

    def _gen_adapted_loop(self, out: TextIO, stmt: TpyForEach, indent: str,
                          iterable_expr: str, elem_type: TpyType,
                          adapter: str, iter_call_expr: str = "",
                          iter_call_fn: Callable[[str], str] | None = None) -> None:
        """Generate begin/end loop with an iter_adapt wrapper.

        Captures the original iterable first (to keep it alive), then wraps
        it with tpy::iter_adapt, then delegates to _gen_begin_end_loop.

        When iter_call_expr is set (e.g. ".__iter__()"), calls it as a suffix
        on the source to get an iterator first.
        When iter_call_fn is set (e.g. lambda src: f"tpy::__iter__({src})"),
        calls it as a function on the source name.

        Produces:
            auto& __src_N = <lvalue_expr>;   // or: auto __src_N = <rvalue_expr>;
            auto __obj_N = tpy::<adapter>(__src_N);
            auto __beg_N = __obj_N.begin();
            ...
        """
        n = self.ctx.iter_counter
        # Don't increment -- _gen_begin_end_loop will allocate its own counter
        src_name = f"__src_{n}"
        src_binding = "auto&" if self._is_lvalue_iterable(stmt.iterable) else "auto"

        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}{src_binding} {src_name} = {iterable_expr};\n")
        if iter_call_fn:
            iter_name = f"__iter_{n}"
            out.write(f"{indent}auto {iter_name} = {iter_call_fn(src_name)};\n")
            adapted_expr = f"tpy::{adapter}({iter_name})"
        elif iter_call_expr:
            # Two-step: call __iter__() on source, then wrap result with adapter
            iter_name = f"__iter_{n}"
            out.write(f"{indent}auto {iter_name} = {src_name}{iter_call_expr};\n")
            adapted_expr = f"tpy::{adapter}({iter_name})"
        else:
            adapted_expr = f"tpy::{adapter}({src_name})"
        self._gen_begin_end_loop(out, stmt, indent, adapted_expr, elem_type, is_lvalue=False)

    def _gen_captured_call_loop(self, out: TextIO, stmt: TpyForEach, indent: str,
                                iterable_expr: str, elem_type: TpyType,
                                make_call: "Callable[[str], str]") -> None:
        """Capture iterable, apply a method/function call, then begin/end loop.

        Used for __iter__() and tpy::as_span() where the container must stay
        alive for the iterator/span to remain valid.

        Produces:
            auto& __src_N = <lvalue_expr>;   // or: auto __src_N = <rvalue_expr>;
            auto __obj_N = __src_N.__iter__();  // (or tpy::as_span(__src_N))
            auto __beg_N = __obj_N.begin();
            ...
        """
        n = self.ctx.iter_counter
        src_name = f"__src_{n}"
        src_binding = "auto&" if self._is_lvalue_iterable(stmt.iterable) else "auto"

        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}{src_binding} {src_name} = {iterable_expr};\n")
        call_expr = make_call(src_name)
        self._gen_begin_end_loop(out, stmt, indent, call_expr, elem_type, is_lvalue=False)

    @staticmethod
    def _unwrap_coerce(expr: TpyExpr) -> TpyExpr:
        """Unwrap TpyCoerce nodes to get the underlying expression."""
        while isinstance(expr, TpyCoerce):
            expr = expr.expr
        return expr

    def _is_lvalue_iterable(self, expr: TpyExpr) -> bool:
        """Check if the iterable expression is a C++ lvalue.

        Lvalue expressions get auto& to preserve consumption semantics.
        Rvalue expressions (constructors, value-returning calls, literals)
        get auto to own the temporary safely.
        """
        expr = self._unwrap_coerce(expr)
        if isinstance(expr, TpyName):
            return True
        if isinstance(expr, TpyFieldAccess):
            return self._is_lvalue_iterable(expr.obj)
        if isinstance(expr, TpySubscript):
            return self._is_lvalue_iterable(expr.obj)
        # Method/function calls returning non-value types use T& in C++ (lvalue).
        # Constructors always produce rvalues.
        # Optional returns use T* (pointer by value, rvalue).
        if isinstance(expr, TpyMethodCall):
            return self._returns_by_ref(expr)
        if isinstance(expr, TpyCall):
            # Constructor calls (generic instantiation or record name) are rvalues
            if expr.call_type is not None:
                return False
            if self.ctx.analyzer.registry.get_record(expr.func):
                return False
            return self._returns_by_ref(expr)
        return False

    def _returns_by_ref(self, expr: TpyExpr) -> bool:
        """Check if a call expression returns by reference (T&) in C++."""
        ret_type = self.types.get_resolved_type(expr)
        return not ret_type.is_value_type() and not isinstance(ret_type, OptionalType)

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
                and expr.func in StatementGenerator._FIXED_INT_NAMES):
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
        assert isinstance(stmt.iterable, TpyCall) and stmt.iterable.func == "range"
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
            out.write(f"{indent}for ({cpp_elem} {var} = {start_expr}; "
                      f"{var} < {stop_expr}; ++{var}) {{\n")
        elif step_kind == "literal_pos":
            if step_val == 1:
                out.write(f"{indent}for ({cpp_elem} {var} = {start_expr}; "
                          f"{var} < {stop_expr}; ++{var}) {{\n")
            else:
                step_cpp = gen_args[2]
                self._gen_range_overflow_check(out, indent, start_expr, stop_expr, step_cpp, elem_type)
                out.write(f"{indent}for ({cpp_elem} {var} = {start_expr}; "
                          f"{var} < {stop_expr}; "
                          f"{var} += {step_cpp}) {{\n")
        elif step_kind == "literal_neg":
            if step_val == -1:
                out.write(f"{indent}for ({cpp_elem} {var} = {start_expr}; "
                          f"{var} > {stop_expr}; --{var}) {{\n")
            else:
                step_cpp = gen_args[2]
                self._gen_range_overflow_check(out, indent, start_expr, stop_expr, step_cpp, elem_type)
                out.write(f"{indent}for ({cpp_elem} {var} = {start_expr}; "
                          f"{var} > {stop_expr}; "
                          f"{var} += {step_cpp}) {{\n")
        else:
            # Variable step -- capture, zero-check, upfront overflow check, ternary condition
            step_cpp = gen_args[2]
            step_temp = f"__step_{n}"
            out.write(f"{indent}{cpp_elem} {step_temp} = {step_cpp};\n")
            step_cpp = step_temp
            out.write(f'{indent}if ({step_cpp} == 0) tpy::tpy_panic("range() arg 3 must not be zero");\n')
            self._gen_range_overflow_check(out, indent, start_expr, stop_expr, step_cpp, elem_type)
            out.write(f"{indent}for ({cpp_elem} {var} = {start_expr}; "
                      f"{step_cpp} > 0 ? {var} < {stop_expr} : {var} > {stop_expr}; "
                      f"{var} += {step_cpp}) {{\n")

        self._gen_loop_body(out, stmt, indent, elem_type)
        return True

    def _gen_range_overflow_check(self, out: TextIO, indent: str,
                                    start_expr: str, stop_expr: str,
                                    step_expr: str, elem_type: TpyType) -> None:
        """Emit upfront overflow check for fixed-int range loops with step != ±1."""
        if isinstance(elem_type, FixedIntType):
            cpp_t = elem_type.to_cpp()
            out.write(f"{indent}tpy::range_check_overflow<{cpp_t}>({start_expr}, {stop_expr}, {step_expr});\n")

    def _gen_for_each(self, out: TextIO, stmt: TpyForEach, indent: str) -> None:
        """Generate a for-each loop over a collection or iterator.

        All iteration uses a single canonical begin/end loop shape.
        The iterable expression is wrapped with an adapter if needed:
        - Enum: tpy::EnumUtil<E>::members
        - Protocol Iterator[T]: tpy::iter_adapt(expr)
        - Protocol Iterable[T]: tpy::__iter__(expr) + tpy::iter_adapt(iter)
        - Protocol ReadOnlySpanLike[T]: tpy::as_span(expr)
        - range(): counter optimization first, else tpy::Range<T>(args...)
        - OptIterator types: tpy::iter_adapt(expr)
        - __iter__()-based types: expr.__iter__() or tpy::iter_adapt(expr.__iter__())
        - Native C++ ranges (dict, set, str, etc.): expr directly
        """
        # Enum iteration: `for c in Color` -> range over EnumUtil<Color>::members
        if stmt.enum_iterable is not None:
            enum_type = stmt.enum_iterable
            cpp_type = enum_type.to_cpp()
            iterable = f"tpy::EnumUtil<{cpp_type}>::members"
            self._gen_begin_end_loop(out, stmt, indent, iterable, enum_type)
            return

        from tpyc.modules import get_native_iterator_element_type, get_iter_info
        iterable_type = self.types.get_resolved_type(stmt.iterable)

        # Resolve sema-stored elem_type (handles PendingStrType -> concrete)
        sema_elem = self.types.resolve_type(stmt.elem_type) if stmt.elem_type else None

        # Resolve TypeParamRef to its bound for protocol-based iteration
        resolved_type = iterable_type
        if isinstance(iterable_type, TypeParamRef):
            bound = self.ctx.current_type_param_bounds.get(iterable_type.name)
            if bound is not None and is_protocol_type(bound):
                resolved_type = bound

        # Handle protocol-typed iterables (Iterator[T], Iterable[T])
        if is_protocol_type(resolved_type) and resolved_type.qualified_name() in ("typing.Iterator", "typing.Iterable"):
            elem_type = sema_elem or resolved_type.type_args[0]
            iterable = self.expressions.gen_expr_deref(stmt.iterable)
            if resolved_type.qualified_name() == "typing.Iterator":
                self._gen_adapted_loop(out, stmt, indent, iterable, elem_type, "iter_adapt")
            else:
                # Iterable[T]: call tpy::__iter__() to get iterator, then adapt
                self._gen_adapted_loop(out, stmt, indent, iterable, elem_type,
                                       "iter_adapt",
                                       iter_call_fn=lambda src: f"tpy::__iter__({src})")
            return

        # Handle ReadOnlySpanLike[T] protocol-typed iterables (uses tpy::as_span)
        if is_protocol_type(resolved_type) and resolved_type.qualified_name() == "tpy.ReadOnlySpanLike":
            elem_type = sema_elem or resolved_type.type_args[0]
            iterable = self.expressions.gen_expr_deref(stmt.iterable)
            self._gen_captured_call_loop(out, stmt, indent, iterable, elem_type,
                                         lambda src: f"tpy::as_span({src})")
            return

        # Optimize range() calls to C-style counter loops
        if isinstance(stmt.iterable, TpyCall) and stmt.iterable.func == "range":
            elem_type = sema_elem or iterable_type.get_element_type()
            if elem_type and self._gen_range_counter_loop(out, stmt, indent, elem_type):
                return
            # Counter optimization didn't apply; fall back to Range<T> begin/end
            iterable = self.expressions.gen_expr_deref(stmt.iterable)
            if elem_type:
                self._gen_begin_end_loop(out, stmt, indent, iterable, elem_type, is_lvalue=False)
                return

        # Check for OptIterator types -- wrap with iter_adapt
        iter_elem = get_native_iterator_element_type(iterable_type, registry=self.ctx.analyzer.registry)
        if iter_elem is not None:
            iterable = self.expressions.gen_expr_deref(stmt.iterable)
            self._gen_adapted_loop(out, stmt, indent, iterable, sema_elem or iter_elem, "iter_adapt")
            return

        # Check for __iter__()-based types (preferred over __span__).
        # get_iteration_element_type() is non-None for builtin containers (list, Array,
        # Span, etc.) that have native C++ begin/end -- skip those to preserve mutability.
        iter_info = get_iter_info(iterable_type, registry=self.ctx.analyzer.registry)
        if iter_info is not None and iterable_type.get_iteration_element_type() is None:
            iterable = self.expressions.gen_expr_deref(stmt.iterable)
            elem_type = sema_elem or iter_info.element_type
            if iter_info.iter_is_native:
                # NativeIterable iterator (e.g. SpanIter) -- call __iter__() and iterate directly
                self._gen_captured_call_loop(out, stmt, indent, iterable, elem_type,
                                             lambda src: f"{src}.__iter__()")
            else:
                # OptIterator-based iterator -- call __iter__(), then wrap with iter_adapt.
                # Call .__iter__() directly rather than tpy::__iter__() to avoid one
                # template dispatch level.
                self._gen_adapted_loop(out, stmt, indent, iterable, elem_type,
                                       "iter_adapt", iter_call_expr=".__iter__()")
            return

        # Native C++ range fallback (list, dict, str, Array, Span, __span__-only types, etc.)
        iterable = self.expressions.gen_expr_deref(stmt.iterable)

        # C string literals include the null terminator, so wrap in string_view
        if isinstance(stmt.iterable, TpyStrLiteral):
            iterable = f"std::string_view({iterable})"

        # Determine element type
        if sema_elem is not None:
            elem_type = sema_elem
        elif is_protocol_type(iterable_type):
            if iterable_type.qualified_name() == "tpy.NativeIterable" and iterable_type.type_args:
                elem_type = iterable_type.type_args[0]
            else:
                elem_type = None
        else:
            elem_type = iterable_type.get_iteration_element_type()

        # Resolve IntLiteralType to configured default integer type.
        if isinstance(elem_type, IntLiteralType):
            elem_type = self.ctx.analyzer.ctx.default_int_type

        if elem_type:
            self._gen_begin_end_loop(out, stmt, indent, iterable, elem_type)
        else:
            # Fallback: use auto with begin/end
            n = self.ctx.iter_counter
            self.ctx.iter_counter += 1
            obj_name = f"__obj_{n}"
            beg_name = f"__beg_{n}"
            end_name = f"__end_{n}"
            obj_binding = "auto&" if self._is_lvalue_iterable(stmt.iterable) else "auto"
            self.ctx.temps.flush(out, indent)
            cpp_var = escape_cpp_name(stmt.var)
            out.write(f"{indent}{obj_binding} {obj_name} = {iterable};\n")
            out.write(f"{indent}auto {beg_name} = {obj_name}.begin();\n")
            out.write(f"{indent}auto {end_name} = {obj_name}.end();\n")
            out.write(f"{indent}for (; {beg_name} != {end_name}; ++{beg_name}) {{\n")
            out.write(f"{indent}{INDENT}auto {cpp_var} = *{beg_name};\n")
            self._gen_loop_body(out, stmt, indent, elem_type)
