"""
TurboPython Statement Code Generation

Generates C++ code from TurboPython statements.
"""

from __future__ import annotations
import io
from typing import TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, FloatType,
    ArrayType, ListType, PendingListType, PendingStrType, OwnType, OptionalType,
    NoneType, NamedType, StrType, StrViewType, STR, TupleType,
    INT32, BIGINT, is_protocol_type, FixedIntType, ALL_FIXED_INTS,
    ReadonlyType, unwrap_readonly, unwrap_optional_own, TypeParamRef, UnionType,
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
)
from ..namespace import Namespace
from .context import INDENT, CodeGenError, escape_cpp_name, qualified_cpp_name, expand_cpp_template
from .type_resolution import resolve_stmt_binding_type
from ..prescan import match_is_none

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
                body_buf.write(f"{indent}{cpp_type} {cpp_name} = __param_{cpp_name};\n")
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
            if isinstance(actual, OptionalType) and actual.uses_pointer_repr():
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
                # StrView local returned as str needs explicit conversion
                elif isinstance(ret_type, StrType):
                    expr_type = self.types.get_resolved_type(stmt.value)
                    if isinstance(expr_type, StrViewType):
                        ret_expr = f"std::string({ret_expr})"
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
        """Get param names that need if constexpr guards for Optional[StaticProtocol].

        When an Optional[StaticProtocol] param is narrowed (e.g. `if items is not None:`),
        the narrowing body must be wrapped in `if constexpr (!std::same_as<T_X, std::nullptr_t>)`
        to prevent instantiation of protocol operations on nullptr_t.
        """
        guards = []
        match = match_is_none(condition)
        if match is not None:
            var_name, is_not_none = match
            if is_not_none and '.' not in var_name:
                declared = self.ctx.current_func_params.get(var_name)
                if declared and self.protocols.is_optional_static_protocol(unwrap_readonly(declared)):
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
            if isinstance(target_type, PendingListType):
                info = self.ctx.analyzer.ctx.list_literals.get(target_type.literal_id)
                if info and info.resolved_type:
                    target_type = info.resolved_type
            elif isinstance(target_type, PendingStrType):
                info = self.ctx.analyzer.ctx.str_vars.get(target_type.str_var_id)
                target_type = info.resolved_type if info and info.resolved_type else STR
        return target_type

    def _normalize_decl_type_for_cpp(self, var_type: TpyType) -> TpyType:
        """Normalize declaration type before C++ emission."""
        resolve_lit = self.ctx.analyzer.ctx.default_int_for_literal
        if isinstance(var_type, PendingListType):
            info = self.ctx.analyzer.ctx.list_literals.get(var_type.literal_id)
            if info and info.resolved_type:
                var_type = info.resolved_type
            else:
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
            if resolved_type is None or isinstance(resolved_type, (PendingListType, PendingStrType)):
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

        # list += other_list -> extend in place
        if stmt.is_list_extend:
            target = self.expressions.gen_expr(stmt.target)
            if self.ctx.is_indirect_name(stmt.target):
                target = f"(*{target})"
            value = self.expressions.gen_expr_deref(stmt.value)
            return f"{indent}tpy::list_extend({target}, {value});\n"

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
        # bind by const ref to avoid copying the tuple
        if isinstance(stmt.value, TpyName) and not any(stmt.is_owned):
            out.write(f"{indent}const auto& {tmp} = {value_expr};\n")
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

            # Optional[StaticProtocol] narrowing: wrap body in if constexpr to
            # prevent instantiation of protocol operations when T = nullptr_t
            constexpr_guards = self._get_nullproto_constexpr_guards(node.condition)

            self.ctx.indent_level += 1
            if constexpr_guards:
                inner_indent = self.ctx.indent()
                for gvar in constexpr_guards:
                    out.write(f"{inner_indent}if constexpr (!std::same_as<T_{gvar}, std::nullptr_t>) {{\n")
                    self.ctx.indent_level += 1
            for s in node.then_body:
                self.gen_stmt(out, s)
            self.ctx.emit_block_trailing_comments(out, node.then_body, self.ctx.indent())
            if constexpr_guards:
                for _ in constexpr_guards:
                    self.ctx.indent_level -= 1
                    out.write(f"{self.ctx.indent()}}}\n")
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
            not isinstance(ty, (UnionType, NoneType))
            for ty in type_facts.values()
        )

    def _emit_branch_decls(self, out: TextIO, stmt: TpyIf, indent: str) -> None:
        """Pre-declare variables first declared inside if/elif branches."""
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

        Shared by _gen_iterator_loop, _gen_range_counter_loop, and _gen_for_each.
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

    def _gen_iterator_loop(self, out: TextIO, stmt: TpyForEach, indent: str,
                           iterable_expr: str, elem_type: TpyType) -> None:
        """Generate a while-loop for OptIterator types.

        Produces:
            auto& __iter_N = <iterable>;   // variable -- reference for consumption
            auto  __iter_N = <iterable>;   // temporary -- copy/move for ownership
            while (auto __opt_N = __iter_N.__next_opt__()) {
                int32_t var = *__opt_N;
                // body
            }
        """
        n = self.ctx.iter_counter
        self.ctx.iter_counter += 1
        iter_name = f"__iter_{n}"
        opt_name = f"__opt_{n}"

        binding = "auto&" if self._is_lvalue_iterable(stmt.iterable) else "auto"

        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}{binding} {iter_name} = {iterable_expr};\n")
        out.write(f"{indent}while (auto {opt_name} = {iter_name}.__next_opt__()) {{\n")

        # Declare loop variable inside the while body
        inner_indent = indent + INDENT
        cpp_var = escape_cpp_name(stmt.var)
        if elem_type.is_value_type():
            cpp_elem = elem_type.to_cpp()
            out.write(f"{inner_indent}{cpp_elem} {cpp_var} = *{opt_name};\n")
        else:
            out.write(f"{inner_indent}auto& {cpp_var} = *{opt_name};\n")

        self._gen_loop_body(out, stmt, indent, elem_type)

    def _gen_iter_protocol_loop(self, out: TextIO, stmt: TpyForEach, indent: str,
                                iterable_expr: str, elem_type: TpyType) -> None:
        """Generate a while-loop for types with __iter__() returning an iterator.

        Produces:
            auto& __obj_N = container;              // variable -- reference
            auto  __obj_N = Container(args);        // temporary -- own it
            auto  __iter_N = __obj_N.__iter__();    // always own the iterator
            while (auto __opt_N = __iter_N.__next_opt__()) {
                T x = *__opt_N;
                // body
            }
        """
        n = self.ctx.iter_counter
        self.ctx.iter_counter += 1
        obj_name = f"__obj_{n}"
        iter_name = f"__iter_{n}"
        opt_name = f"__opt_{n}"

        obj_binding = "auto&" if self._is_lvalue_iterable(stmt.iterable) else "auto"

        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}{obj_binding} {obj_name} = {iterable_expr};\n")
        out.write(f"{indent}auto {iter_name} = {obj_name}.__iter__();\n")
        out.write(f"{indent}while (auto {opt_name} = {iter_name}.__next_opt__()) {{\n")

        inner_indent = indent + INDENT
        cpp_var = escape_cpp_name(stmt.var)
        if elem_type.is_value_type():
            cpp_elem = elem_type.to_cpp()
            out.write(f"{inner_indent}{cpp_elem} {cpp_var} = *{opt_name};\n")
        else:
            out.write(f"{inner_indent}auto& {cpp_var} = *{opt_name};\n")

        self._gen_loop_body(out, stmt, indent, elem_type)

    def _gen_span_loop(self, out: TextIO, stmt: TpyForEach, indent: str,
                        iterable_expr: str, elem_type: TpyType,
                        use_as_span: bool = False) -> None:
        """Generate range-based for via __span__() or tpy::as_span().

        Produces:
            auto& __obj_N = container;              // lvalue ref
            auto  __span_N = __obj_N.__span__();    // or tpy::as_span(__obj_N)
            for (ElemT x : __span_N) { ... }        // value types by copy
            for (auto& x : __span_N) { ... }        // non-value types by ref
        """
        n = self.ctx.iter_counter
        self.ctx.iter_counter += 1
        obj_name = f"__obj_{n}"
        span_name = f"__span_{n}"

        obj_binding = "auto&" if self._is_lvalue_iterable(stmt.iterable) else "auto"

        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}{obj_binding} {obj_name} = {iterable_expr};\n")
        if use_as_span:
            out.write(f"{indent}auto {span_name} = tpy::as_span({obj_name});\n")
        else:
            out.write(f"{indent}auto {span_name} = {obj_name}.__span__();\n")

        cpp_var = escape_cpp_name(stmt.var)
        if elem_type.is_value_type():
            cpp_elem = elem_type.to_cpp()
            out.write(f"{indent}for ({cpp_elem} {cpp_var} : {span_name}) {{\n")
        else:
            out.write(f"{indent}for (auto& {cpp_var} : {span_name}) {{\n")

        self._gen_loop_body(out, stmt, indent, elem_type)

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
        """Generate a for-each loop over a collection or iterator."""
        # Enum iteration: `for c in Color` -> range over EnumUtil<Color>::members
        if stmt.enum_iterable is not None:
            enum_type = stmt.enum_iterable
            cpp_type = enum_type.to_cpp()
            out.write(f"{indent}for ({cpp_type} {stmt.var} : tpy::EnumUtil<{cpp_type}>::members) {{\n")
            self.ctx.var_types[stmt.var] = enum_type
            self._gen_loop_body(out, stmt, indent, enum_type)
            return

        from tpyc.modules import get_native_iterator_element_type, get_iter_element_type, get_span_element_type
        iterable_type = self.types.get_resolved_type(stmt.iterable)

        # Resolve TypeParamRef to its bound for protocol-based iteration
        resolved_type = iterable_type
        if isinstance(iterable_type, TypeParamRef):
            bound = self.ctx.current_type_param_bounds.get(iterable_type.name)
            if bound is not None and is_protocol_type(bound):
                resolved_type = bound

        # Handle protocol-typed iterables (Iterator[T], Iterable[T])
        if is_protocol_type(resolved_type) and resolved_type.name in ("Iterator", "Iterable"):
            elem_type = resolved_type.type_args[0]
            iterable = self.expressions.gen_expr_deref(stmt.iterable)
            if resolved_type.name == "Iterator":
                self._gen_iterator_loop(out, stmt, indent, iterable, elem_type)
            else:
                self._gen_iter_protocol_loop(out, stmt, indent, iterable, elem_type)
            return

        # Handle ReadOnlySpanLike[T] protocol-typed iterables (uses tpy::as_span)
        if is_protocol_type(resolved_type) and resolved_type.name == "ReadOnlySpanLike":
            elem_type = resolved_type.type_args[0]
            iterable = self.expressions.gen_expr_deref(stmt.iterable)
            self._gen_span_loop(out, stmt, indent, iterable, elem_type, use_as_span=True)
            return

        # Optimize range() calls to C-style counter loops (before protocol checks)
        if isinstance(stmt.iterable, TpyCall) and stmt.iterable.func == "range":
            elem_type = iterable_type.get_element_type()
            if elem_type and self._gen_range_counter_loop(out, stmt, indent, elem_type):
                return

        # Check for OptIterator types -- these use while-loop codegen
        iter_elem = get_native_iterator_element_type(iterable_type, registry=self.ctx.analyzer.registry)
        if iter_elem is not None:
            iterable = self.expressions.gen_expr_deref(stmt.iterable)
            self._gen_iterator_loop(out, stmt, indent, iterable, iter_elem)
            return

        # Check for __span__()-based types (zero-cost range-based for via span)
        span_elem = get_span_element_type(iterable_type, registry=self.ctx.analyzer.registry)
        if span_elem is not None:
            iterable = self.expressions.gen_expr_deref(stmt.iterable)
            self._gen_span_loop(out, stmt, indent, iterable, span_elem)
            return

        # Check for __iter__()-based types (container -> separate iterator)
        iter_elem = get_iter_element_type(iterable_type, registry=self.ctx.analyzer.registry)
        if iter_elem is not None:
            iterable = self.expressions.gen_expr_deref(stmt.iterable)
            self._gen_iter_protocol_loop(out, stmt, indent, iterable, iter_elem)
            return

        iterable = self.expressions.gen_expr_deref(stmt.iterable)

        # C string literals include the null terminator in range-based for,
        # so wrap them in std::string_view to iterate only the characters.
        if isinstance(stmt.iterable, TpyStrLiteral):
            iterable = f"std::string_view({iterable})"

        # Determine element type for the loop variable
        # Handle protocol types (e.g., NativeIterable[T])
        if is_protocol_type(iterable_type):
            if iterable_type.name == "NativeIterable" and iterable_type.type_args:
                elem_type = iterable_type.type_args[0]
            else:
                elem_type = None  # Will use auto
        else:
            elem_type = iterable_type.get_iteration_element_type()

        # Resolve IntLiteralType to configured default integer type.
        if isinstance(elem_type, IntLiteralType):
            elem_type = self.ctx.analyzer.ctx.default_int_type

        # Flush any pending temps before for loop header
        self.ctx.temps.flush(out, indent)

        # Generate C++ range-based for loop
        # Non-value element types use auto& (reference into container, not pointer-local)
        cpp_var = escape_cpp_name(stmt.var)
        if elem_type:
            if not elem_type.is_value_type():
                out.write(f"{indent}for (auto& {cpp_var} : {iterable}) {{\n")
            else:
                cpp_type = elem_type.to_cpp()
                out.write(f"{indent}for ({cpp_type} {cpp_var} : {iterable}) {{\n")
            # Track the loop variable's type for use in body expressions
            self.ctx.var_types[stmt.var] = elem_type
        else:
            # Fallback: use auto
            out.write(f"{indent}for (auto {cpp_var} : {iterable}) {{\n")

        self._gen_loop_body(out, stmt, indent, elem_type)
