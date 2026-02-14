"""
TurboPython Statement Code Generation

Generates C++ code from TurboPython statements.
"""

from __future__ import annotations
import io
from typing import TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, FloatType,
    ArrayType, ListType, PendingListType, OwnType, OptionalType, NoneType, NamedType, StrType,
    INT32, BIGINT, is_protocol_type,
)
from ..parse import (
    TpyStmt, TpyVarDecl, TpyAssign, TpyAugAssign, TpyExprStmt, TpyReturn,
    TpyIf, TpyWhile, TpyForEach, TpyBreak, TpyContinue, TpyPassStmt, TpyRaiseStopIteration,
    TpyGlobal,
    TpyImport, TpySubscript, TpyStrLiteral, TpyNoneLiteral, TpyName, TpyExpr, TpyFunction,
    TpyAssert, TpyBoolLiteral,
    TpyFieldAccess, TpyMethodCall,
    TpyCall, TpyIntLiteral, TpyUnaryOp, TpyCoerce,
)
from ..namespace import Namespace
from .context import CodeGenError, module_to_cpp_namespace, expand_cpp_template
from .type_resolution import resolve_stmt_binding_type

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver
    from .expressions import ExpressionGenerator
    from .builtins import BuiltinGenerator


class StatementGenerator:
    """Generates C++ code from TurboPython statements."""

    def __init__(
        self,
        ctx: CodeGenContext,
        types: TypeResolver,
        builtins: BuiltinGenerator,
    ):
        self.ctx = ctx
        self.types = types
        self.builtins = builtins
        # Will be set after expressions is created
        self.expressions: ExpressionGenerator | None = None

    def set_expressions(self, expressions: ExpressionGenerator):
        """Set expressions generator (to break circular dependency)."""
        self.expressions = expressions

    def _gen_buffered_body(self, out: TextIO, stmts: list[TpyStmt],
                           track_stmt_line: bool = False) -> None:
        """Buffer body statements, prepend hoist declarations, write to output."""
        body_buf = io.StringIO()
        for stmt in stmts:
            if track_stmt_line:
                self.ctx.current_stmt_line = stmt.loc.line if hasattr(stmt, 'loc') and stmt.loc else 0
            self.gen_stmt(body_buf, stmt)
        if track_stmt_line:
            self.ctx.current_stmt_line = 0
        # Hoist decls have base indent "  "; add extra for nested scopes (methods)
        hoist_prefix = "  " * (self.ctx.indent_level - 1)
        for decl in self.ctx.pending_hoist_decls:
            out.write(f"{hoist_prefix}{decl}")
        out.write(body_buf.getvalue())

    def gen_body(self, out: TextIO, body: list[TpyStmt],
                 params: list[tuple[str, TpyType]], return_type: TpyType,
                 func: TpyFunction, local_ns: Namespace,
                 indent_level: int = 1, is_method: bool = False) -> None:
        """Generate the body of a function or method.

        Handles scope setup, body buffering, hoist-decl prepending, and cleanup.
        Shared by gen_function_def() and _gen_method().
        """
        self.ctx.reset_scope()
        self.ctx.declared_vars = {pname for pname, _ in params}
        self.ctx.var_types = {pname: ptype for pname, ptype in params}
        self.ctx.local_scope_names = {pname for pname, _ in params}
        self.ctx.global_declared_vars = self.ctx.analyzer.function_global_decls.get(id(func), set())
        self.ctx.reassigned_vars, self.ctx.rvalue_reassigned_vars = self.scan_reassigned_vars(body)
        self.ctx.hoisted_vars = self.ctx.analyzer.function_hoisted_vars.get(id(func), set())
        # Optional non-value params are T* in C++ — need pointer-local treatment (->)
        for pname, ptype in params:
            if isinstance(ptype, OptionalType) and not ptype.inner.is_value_type():
                self.ctx.pointer_locals.add(pname)
        self.ctx.current_ns = local_ns
        self.ctx.indent_level = indent_level
        self.ctx.current_return_type = return_type
        self.ctx.current_func_params = {pname: ptype for pname, ptype in params}
        if is_method:
            self.ctx.in_method = True

        self._gen_buffered_body(out, body)

        if is_method:
            self.ctx.in_method = False
        self.ctx.local_scope_names = set()
        self.ctx.indent_level = 0
        self.ctx.current_ns = None

    def scan_reassigned_vars(self, stmts: list[TpyStmt]) -> tuple[set[str], set[str]]:
        """Pre-scan a function body to find variables that are reassigned after first declaration.

        Returns (reassigned, rvalue_reassigned) where rvalue_reassigned is the
        subset that has at least one rvalue reassignment (call, constructor, etc.).
        """
        declared: set[str] = set()
        reassigned: set[str] = set()
        rvalue_reassigned: set[str] = set()
        self._scan_stmts(stmts, declared, reassigned, rvalue_reassigned)
        # Global-declared vars are not local reassignments
        reassigned -= self.ctx.global_declared_vars
        rvalue_reassigned -= self.ctx.global_declared_vars
        return reassigned, rvalue_reassigned

    @staticmethod
    def _is_scan_rvalue(expr: TpyExpr | None) -> bool:
        """Conservative rvalue check for pre-scan (no type registry needed)."""
        if expr is None:
            return False
        from ..parse import (TpyCall, TpyBinOp, TpyUnaryOp, TpyMethodCall,
                             TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
                             TpyBoolLiteral, TpyArrayLiteral, TpyListRepeat,
                             TpyCoerce)
        if isinstance(expr, TpyCoerce):
            return StatementGenerator._is_scan_rvalue(expr.expr)
        if isinstance(expr, (TpyName, TpySubscript)):
            return False
        from ..parse import TpyFieldAccess
        if isinstance(expr, TpyFieldAccess):
            return StatementGenerator._is_scan_rvalue(expr.obj)
        return isinstance(expr, (TpyCall, TpyBinOp, TpyUnaryOp, TpyMethodCall,
                                 TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
                                 TpyBoolLiteral, TpyArrayLiteral, TpyListRepeat))

    def _scan_stmts(self, stmts: list[TpyStmt], declared: set[str],
                    reassigned: set[str], rvalue_reassigned: set[str]) -> None:
        for stmt in stmts:
            if isinstance(stmt, TpyVarDecl):
                if stmt.name in declared:
                    reassigned.add(stmt.name)
                    if self._is_scan_rvalue(stmt.init):
                        rvalue_reassigned.add(stmt.name)
                else:
                    declared.add(stmt.name)
            elif isinstance(stmt, TpyAssign):
                if isinstance(stmt.target, TpyName) and stmt.target.name in declared:
                    reassigned.add(stmt.target.name)
                    if self._is_scan_rvalue(stmt.value):
                        rvalue_reassigned.add(stmt.target.name)
            if isinstance(stmt, TpyIf):
                self._scan_stmts(stmt.then_body, declared, reassigned, rvalue_reassigned)
                self._scan_stmts(stmt.else_body, declared, reassigned, rvalue_reassigned)
            elif isinstance(stmt, (TpyWhile, TpyForEach)):
                self._scan_stmts(stmt.body, declared, reassigned, rvalue_reassigned)

    def gen_stmt(self, out: TextIO, stmt: TpyStmt) -> None:
        """Generate a statement."""
        indent = self.ctx.indent()

        # Track current line for order-aware import qualification in top-level context
        # (current_stmt_line > 0 means we're in top-level, set by gen_module_init)
        if self.ctx.current_stmt_line > 0 and hasattr(stmt, 'loc') and stmt.loc:
            self.ctx.current_stmt_line = stmt.loc.line

        self.ctx.emit_source_comment(out, stmt.loc, indent)

        # Compound statements - delegate to handlers (they flush before their header)
        if isinstance(stmt, TpyIf):
            self._gen_if(out, stmt, indent)
        elif isinstance(stmt, TpyWhile):
            self._gen_while(out, stmt, indent)
        elif isinstance(stmt, TpyForEach):
            self._gen_for_each(out, stmt, indent)
        else:
            # Simple statements - single flush point for all
            code = self._gen_simple_stmt(stmt, indent)
            if code is not None:
                self.ctx.temps.flush(out, indent)
                out.write(code)

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
        elif isinstance(stmt, TpyExprStmt):
            if isinstance(stmt.expr, TpyStrLiteral):
                return None  # Skip docstrings
            return f"{indent}{self.expressions.gen_expr(stmt.expr)};\n"
        elif isinstance(stmt, TpyReturn):
            if stmt.value:
                ret_type = self.ctx.current_return_type
                if isinstance(ret_type, OptionalType):
                    if ret_type.inner.is_value_type():
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
                        # Already a pointer — return as-is
                        return f"{indent}return {ret_expr};\n"
                    # Field access with non-value Optional produces std::optional<T>, convert to T*
                    if isinstance(stmt.value, TpyFieldAccess):
                        val_type = self.ctx.analyzer.get_expr_type(stmt.value)
                        if isinstance(val_type, OptionalType) and not val_type.inner.is_value_type():
                            return f"{indent}return tpy::optional_to_ptr({ret_expr});\n"
                    # Take address of lvalue
                    return f"{indent}return &({ret_expr});\n"
                ret_expr = self.expressions.gen_expr(stmt.value, ret_type)
                # Dereference pointer-locals/pointer-globals on return (T* → T&)
                if self.ctx.is_indirect_name(stmt.value):
                    ret_expr = f"(*{ret_expr})"
                return f"{indent}return {ret_expr};\n"
            return f"{indent}return;\n"
        elif isinstance(stmt, TpyBreak):
            return f"{indent}break;\n"
        elif isinstance(stmt, TpyContinue):
            return f"{indent}continue;\n"
        elif isinstance(stmt, TpyPassStmt):
            return ""  # No-op - emit nothing
        elif isinstance(stmt, TpyGlobal):
            return ""  # No C++ output — just a sema directive
        elif isinstance(stmt, TpyRaiseStopIteration):
            return f"{indent}return std::nullopt;\n"
        elif isinstance(stmt, TpyAssert):
            # Constant-fold trivially-known assertions.
            if isinstance(stmt.condition, TpyBoolLiteral):
                if stmt.condition.value:
                    return ""
                if stmt.message is not None and isinstance(stmt.message, TpyStrLiteral):
                    msg = stmt.message.value.replace("\\", "\\\\").replace('"', '\\"')
                    return f'{indent}tpy::tpy_panic("{msg}");\n'
                return f'{indent}tpy::tpy_panic("assertion failed");\n'
            if isinstance(stmt.condition, TpyNoneLiteral):
                if stmt.message is not None and isinstance(stmt.message, TpyStrLiteral):
                    msg = stmt.message.value.replace("\\", "\\\\").replace('"', '\\"')
                    return f'{indent}tpy::tpy_panic("{msg}");\n'
                return f'{indent}tpy::tpy_panic("assertion failed");\n'
            # Use Python-style truthiness conversion for assert conditions.
            bool_cond = self.expressions.gen_truthy_expr(stmt.condition)
            if stmt.message is not None and isinstance(stmt.message, TpyStrLiteral):
                msg = stmt.message.value.replace("\\", "\\\\").replace('"', '\\"')
                return f'{indent}if (!({bool_cond})) tpy::tpy_panic("{msg}");\n'
            return f'{indent}if (!({bool_cond})) tpy::tpy_panic("assertion failed");\n'
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
                        result += f"{indent}{module_to_cpp_namespace(parent_pkg)}::__tpy_init();\n"
                # Then init the submodule itself (skip self-init)
                if stmt.module_name != self.ctx.module_name:
                    result += f"{indent}{module_to_cpp_namespace(stmt.module_name)}::__tpy_init();\n"
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
        if isinstance(target_type, OptionalType) and not target_type.inner.is_value_type():
            return True
        if target_type.is_value_type():
            return False
        if name in self.ctx.reassigned_vars:
            return True
        if name in self.ctx.hoisted_vars:
            return True
        if init is not None and not self.ctx.is_rvalue_source(init):
            return True
        return False

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
            if isinstance(target_type, OwnType):
                target_type = target_type.wrapped
            if isinstance(target_type, IntLiteralType):
                target_type = BIGINT
            elif isinstance(target_type, ArrayType) and isinstance(target_type.element_type, IntLiteralType):
                target_type = ArrayType(BIGINT, target_type.size)
        return target_type

    def _normalize_decl_type_for_cpp(self, var_type: TpyType) -> TpyType:
        """Normalize declaration type before C++ emission."""
        if isinstance(var_type, IntLiteralType):
            var_type = BIGINT
        elif isinstance(var_type, ArrayType) and isinstance(var_type.element_type, IntLiteralType):
            var_type = ArrayType(BIGINT, var_type.size)
        elif isinstance(var_type, (ListType, PendingListType)):
            elem = getattr(var_type, "element_type", None)
            if isinstance(elem, IntLiteralType):
                var_type = ListType(BIGINT)
        # Optional non-value types use inner type (pointer-local adds T*)
        if isinstance(var_type, OptionalType) and not var_type.inner.is_value_type():
            var_type = var_type.inner
        return var_type

    def _cpp_decl_type(self, var_type: TpyType) -> str:
        """Return C++ declaration type name for a normalized semantic type."""
        normalized = self._normalize_decl_type_for_cpp(var_type)
        if self.ctx.contains_protocol_type(normalized):
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
            if resolved_type is None or isinstance(resolved_type, PendingListType):
                resolved_type = self.ctx.analyzer.get_expr_type(stmt.init)
            if resolved_type is None:
                raise CodeGenError(
                    f"Could not infer type for variable '{stmt.name}'", loc=stmt.loc
                )
            return self._cpp_decl_type(resolved_type)
        raise CodeGenError(f"Variable '{stmt.name}' has no type annotation and no initializer", loc=stmt.loc)

    # --- Rvalue slot helpers (shared by init and rebind) ---

    @staticmethod
    def _ptr_from_rvalue_slot(slot: str, init_expr: str, is_opt_field: bool) -> str:
        """Assign rvalue into pre-declared slot and derive pointer expression."""
        if is_opt_field:
            return f"tpy::optional_to_ptr({slot} = {init_expr})"
        return f"&*({slot} = {init_expr})"

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
        - None literal → nullptr
        - OptionalType source (function returning T*) → direct pointer copy
        - rvalue → new slot + take address
        - pointer-local name → pointer copy
        - lvalue ref (param, subscript, field) → take address

        For vars with future rvalue rebinds, a separate rebind slot is
        pre-declared so aliases to the init value aren't overwritten.
        """
        from ..parse import TpyName as _TpyName

        # None literal → nullptr
        if isinstance(init, TpyNoneLiteral):
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
            return f"{rebind_decl}{indent}{cpp_type}* {name} = nullptr;\n"

        init_type = self.ctx.analyzer.get_expr_type(init)
        # Optional non-value field on lvalue object → optional_to_ptr directly
        # Optional non-value non-field source → T* pass-through
        # Optional non-value field on rvalue → falls through to rvalue path
        is_opt_field = (isinstance(init_type, OptionalType)
                        and not init_type.inner.is_value_type()
                        and isinstance(init, TpyFieldAccess))
        if isinstance(init_type, OptionalType) and not init_type.inner.is_value_type():
            if isinstance(init, TpyFieldAccess):
                if not self.ctx.is_rvalue_source(init):
                    init_expr = self.expressions.gen_expr(init, target_type)
                    return f"{indent}{cpp_type}* {name} = tpy::optional_to_ptr({init_expr});\n"
                # rvalue field: fall through to rvalue path
            else:
                init_expr = self.expressions.gen_expr(init, target_type)
                return f"{indent}{cpp_type}* {name} = {init_expr};\n"

        init_expr = self.expressions.gen_expr(init, target_type)

        is_hoisted = name in self.ctx.hoisted_vars
        static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
        # Hoisted decls go to function scope — use global_scope flag from slot state
        hoist_static_kw = "static " if self.ctx.slots.global_scope else ""
        slot_opt_cpp = f"std::optional<{cpp_type}>"
        target = f"{cpp_type}* {name}"
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
                deref = self._ptr_from_rvalue_slot(init_slot, init_expr, is_opt_field)
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
            return f"{rebind_decl}{indent}{cpp_type}* {name} = {init_expr};\n"
        elif self.ctx._is_pointer_global(init):
            return f"{rebind_decl}{indent}{cpp_type}* {name} = {init_expr};\n"
        elif self.ctx.is_global_name(init):
            return f"{rebind_decl}{indent}{cpp_type}* {name} = &({init_expr});\n"
        else:
            # lvalue ref: param, subscript, field → take address
            return f"{rebind_decl}{indent}{cpp_type}* {name} = &({init_expr});\n"

    def _gen_pointer_local_rebind(self, name: str, cpp_type: str, init: 'TpyExpr',
                                   target_type: TpyType | None, indent: str) -> str:
        """Generate pointer-local rebinding code (reassignment).

        For rvalue sources, reuses the rebind slot declared at init site
        to avoid creating loop-scoped storage that would dangle.
        """
        from ..parse import TpyName as _TpyName

        # None literal → set to nullptr
        if isinstance(init, TpyNoneLiteral):
            return f"{indent}{name} = nullptr;\n"

        init_type = self.ctx.analyzer.get_expr_type(init)
        # Optional non-value field on lvalue → optional_to_ptr directly
        # Optional non-value non-field source → T* pass-through
        # Optional non-value field on rvalue → falls through to rvalue path
        is_opt_field = (isinstance(init_type, OptionalType)
                        and not init_type.inner.is_value_type()
                        and isinstance(init, TpyFieldAccess))
        if isinstance(init_type, OptionalType) and not init_type.inner.is_value_type():
            if isinstance(init, TpyFieldAccess):
                if not self.ctx.is_rvalue_source(init):
                    init_expr = self.expressions.gen_expr(init, target_type)
                    return f"{indent}{name} = tpy::optional_to_ptr({init_expr});\n"
                # rvalue field: fall through to rvalue path
            else:
                init_expr = self.expressions.gen_expr(init, target_type)
                return f"{indent}{name} = {init_expr};\n"

        init_expr = self.expressions.gen_expr(init, target_type)

        is_hoisted = name in self.ctx.hoisted_vars
        static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
        hoist_static_kw = "static " if self.ctx.slots.global_scope else ""
        slot_opt_cpp = f"std::optional<{cpp_type}>"
        if self.ctx.is_rvalue_source(init):
            rebind_slot = self.ctx.rebind_slots.get(name)
            if rebind_slot:
                deref = self._ptr_from_rvalue_slot(rebind_slot, init_expr, is_opt_field)
                return f"{indent}{name} = {deref};\n"
            # First rvalue assignment (e.g. global init) — declare slot here
            slot = self.ctx.slots.next_slot()
            self.ctx.rebind_slots[name] = slot
            slot_type = self._slot_decl_type(cpp_type, is_opt_field)
            if is_hoisted:
                self.ctx.pending_hoist_decls.append(f"  {hoist_static_kw}{slot_opt_cpp} {slot};\n")
                deref = self._ptr_from_rvalue_slot(slot, init_expr, is_opt_field)
                return f"{indent}{name} = {deref};\n"
            deref = self._ptr_from_local_slot(slot, is_opt_field)
            return (f"{indent}{static_kw}{slot_type} {slot} = {init_expr};\n"
                    f"{indent}{name} = {deref};\n")
        elif isinstance(init, _TpyName) and init.name in self.ctx.pointer_locals:
            return f"{indent}{name} = {init_expr};\n"
        elif self.ctx._is_pointer_global(init):
            return f"{indent}{name} = {init_expr};\n"
        elif self.ctx.is_global_name(init):
            return f"{indent}{name} = &({init_expr});\n"
        else:
            return f"{indent}{name} = &({init_expr});\n"

    def _gen_var_decl_code(self, stmt: TpyVarDecl, indent: str) -> str | None:
        """Generate code for a variable declaration. Returns code to write or None."""
        from ..parse.nodes import VarLinkage
        if stmt.linkage != VarLinkage.DEFAULT:
            return None
        # Global-declared vars: emit assignment to the existing global, not a local decl
        if stmt.name in self.ctx.global_declared_vars:
            if not stmt.init:
                return None
            var_type = self.ctx.analyzer.get_expr_type(stmt.init)
            init_expr = self.expressions.gen_expr(stmt.init, var_type)
            return f"{indent}{stmt.name} = {init_expr};\n"

        # Check if variable is already declared (reassignment)
        if stmt.name in self.ctx.declared_vars:
            if stmt.init:
                var_type = self.ctx.var_types.get(stmt.name)
                # Pointer-local reassignment
                if stmt.name in self.ctx.pointer_locals:
                    # OptionalType uses inner type (pointer-local adds T*)
                    resolve_type = var_type
                    if isinstance(var_type, OptionalType) and not var_type.inner.is_value_type():
                        resolve_type = var_type.inner
                    cpp_type = self.types.type_to_cpp(resolve_type) if resolve_type else "auto"
                    return self._gen_pointer_local_rebind(stmt.name, cpp_type, stmt.init, var_type, indent)
                init_expr = self.expressions.gen_expr(stmt.init, var_type)
                return f"{indent}{stmt.name} = {init_expr};\n"
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

        # Indirection for non-value types in function/method scope
        if self._needs_indirection(target_type, stmt.name, stmt.init):
            is_optional = isinstance(target_type, OptionalType)
            if not is_optional:
                assert stmt.init, f"indirect local '{stmt.name}' missing initializer"
            if is_optional or stmt.name in self.ctx.reassigned_vars or stmt.name in self.ctx.hoisted_vars:
                # T* pointer-local — needs rebinding support (or hoisted storage)
                self.ctx.pointer_locals.add(stmt.name)
                if stmt.init:
                    return self._gen_pointer_local_init(stmt.name, cpp_type, stmt.init, target_type, indent)
                else:
                    # Optional without initializer → nullptr
                    return f"{indent}{cpp_type}* {stmt.name} = nullptr;\n"
            else:
                # T& reference — alias without rebinding
                init_expr = self.expressions.gen_expr_deref(stmt.init, target_type)
                return f"{indent}{cpp_type}& {stmt.name} = {init_expr};\n"

        if stmt.init:
            init_expr = self.expressions.gen_expr(stmt.init, target_type)
            return f"{indent}{cpp_type} {stmt.name} = {init_expr};\n"
        else:
            return f"{indent}{cpp_type} {stmt.name};\n"

    def _gen_assign_code(self, stmt: TpyAssign, indent: str) -> str:
        """Generate code for an assignment. Returns code to write."""
        # Special handling for subscript assignment
        if isinstance(stmt.target, TpySubscript):
            obj = self.expressions.gen_expr(stmt.target.obj)
            target_type = self.ctx.analyzer.get_expr_type(stmt.target)
            value = self.expressions.gen_expr(stmt.value, target_type)
            obj_type = self.ctx.analyzer.get_expr_type(stmt.target.obj)
            index_type = self.ctx.analyzer.get_expr_type(stmt.target.index)
            # Dereference globals for subscript access
            subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(stmt.target.obj) else obj
            index_expr = self.expressions.gen_index_expr(subscript_obj, stmt.target.index, index_type)

            # Use registry lookup for __setitem__
            cpp_template = self.builtins.get_type_method_template(obj_type, "__setitem__")
            if cpp_template:
                code = expand_cpp_template(cpp_template, subscript_obj, index_expr, value)
                return f"{indent}{code};\n"
            else:
                return f"{indent}{subscript_obj}[{index_expr}] = {value};\n"

        # Pointer-local rebinding (e.g., x.field = ... where x is pointer-local handled by field access)
        if isinstance(stmt.target, TpyName) and stmt.target.name in self.ctx.pointer_locals:
            target_type = self.ctx.var_types.get(stmt.target.name)
            cpp_type = self.types.type_to_cpp(target_type) if target_type else "auto"
            return self._gen_pointer_local_rebind(stmt.target.name, cpp_type, stmt.value, target_type, indent)

        # Assignment to optional field: std::optional<T> storage needs boundary conversion
        if isinstance(stmt.target, TpyFieldAccess):
            target_type = self.ctx.analyzer.get_expr_type(stmt.target)
            if isinstance(target_type, OptionalType) and not target_type.inner.is_value_type():
                target = self.expressions.gen_expr(stmt.target)
                # Value source is T* (pointer-local, function returning Optional) → wrap
                if self.ctx.is_indirect_name(stmt.value):
                    value = self.expressions.gen_expr(stmt.value)
                    return f"{indent}{target} = tpy::ptr_to_optional({value});\n"
                raw_val_type = self.ctx.analyzer.get_expr_type(stmt.value)
                val_type = raw_val_type.wrapped if isinstance(raw_val_type, OwnType) else raw_val_type
                source = self.ctx.unwrap_copy(stmt.value)
                if isinstance(val_type, OptionalType) and not isinstance(source, TpyFieldAccess):
                    # Own[T] | None returns std::optional<T> — direct assign
                    # T | None returns T* — needs ptr_to_optional wrapping
                    is_owned_optional = (isinstance(val_type, OptionalType)
                                         and isinstance(val_type.inner, OwnType))
                    value = self.expressions.gen_expr(stmt.value, target_type)
                    if is_owned_optional:
                        return f"{indent}{target} = {value};\n"
                    return f"{indent}{target} = tpy::ptr_to_optional({value});\n"
                # Direct value or optional-to-optional (field-to-field) works without conversion
                value = self.expressions.gen_expr_deref(stmt.value, target_type)
                return f"{indent}{target} = {value};\n"

        # Default: simple assignment
        target = self.expressions.gen_expr(stmt.target)
        target_type = self.ctx.analyzer.get_expr_type(stmt.target)
        value = self.expressions.gen_expr_deref(stmt.value, target_type)
        return f"{indent}{target} = {value};\n"

    def _gen_aug_assign_code(self, stmt: TpyAugAssign, indent: str) -> str:
        """Generate code for an augmented assignment. Returns code to write."""
        # Special handling for subscript targets - use set_value() pattern
        if isinstance(stmt.target, TpySubscript):
            return self._gen_aug_assign_subscript_code(stmt, indent)

        target = self.expressions.gen_expr(stmt.target)
        target_type = self.ctx.analyzer.get_expr_type(stmt.target)
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
        index_type = self.ctx.analyzer.get_expr_type(subscript.index)
        # Dereference globals for subscript access
        subscript_obj = f"(*{obj})" if self.ctx.is_indirect_name(subscript.obj) else obj
        index_expr = self.expressions.gen_index_expr(subscript_obj, subscript.index, index_type or INT32)

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
            return f"{indent}{subscript_obj}[{index_expr}] = {result_expr};\n"

    def _gen_if(self, out: TextIO, stmt: TpyIf, indent: str) -> None:
        """Generate an if statement."""
        # Pre-declare variables first declared inside branches
        branch_decls = self.ctx.analyzer.if_branch_decls.get(id(stmt), {})
        for name, var_type in branch_decls.items():
            if name not in self.ctx.declared_vars:
                # OptionalType uses inner type (pointer-local adds T*)
                resolve_type = var_type
                if isinstance(var_type, OptionalType) and not var_type.inner.is_value_type():
                    resolve_type = var_type.inner
                cpp_type = self.types.type_to_cpp(resolve_type)
                self.ctx.declared_vars.add(name)
                self.ctx.local_scope_names.add(name)
                self.ctx.var_types[name] = var_type
                if self.ctx.current_ns and var_type:
                    self.ctx.current_ns.bind_variable(name, var_type)
                if self._needs_indirection(var_type, name, None):
                    self.ctx.pointer_locals.add(name)
                    if name in self.ctx.rvalue_reassigned_vars:
                        static_kw = "static " if self.ctx.current_ns is self.ctx.analyzer.global_ns else ""
                        slot = self.ctx.slots.next_slot()
                        self.ctx.rebind_slots[name] = slot
                        out.write(f"{indent}{static_kw}std::optional<{cpp_type}> {slot};\n")
                    out.write(f"{indent}{cpp_type}* {name};\n")
                else:
                    out.write(f"{indent}{cpp_type} {name};\n")

        cond = self.expressions.gen_truthy_expr(stmt.condition)
        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}if ({cond}) {{\n")

        self.ctx.indent_level += 1
        for s in stmt.then_body:
            self.gen_stmt(out, s)
        self.ctx.indent_level -= 1

        if stmt.else_body:
            out.write(f"{indent}}} else {{\n")
            self.ctx.indent_level += 1
            for s in stmt.else_body:
                self.gen_stmt(out, s)
            self.ctx.indent_level -= 1

        out.write(f"{indent}}}\n")

    def _gen_while(self, out: TextIO, stmt: TpyWhile, indent: str) -> None:
        """Generate a while loop."""
        cond = self.expressions.gen_truthy_expr(stmt.condition)
        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}while ({cond}) {{\n")

        self.ctx.indent_level += 1
        for s in stmt.body:
            self.gen_stmt(out, s)
        self.ctx.indent_level -= 1

        out.write(f"{indent}}}\n")

    def _gen_loop_body(self, out: TextIO, stmt: TpyForEach, indent: str,
                        elem_type: TpyType | None) -> None:
        """Generate loop body statements with namespace/scope tracking.

        Shared by _gen_iterator_loop, _gen_range_counter_loop, and _gen_for_each.
        Writes the body statements, the closing brace, and cleans up the loop
        variable from var_types.
        """
        self.ctx.local_scope_names.add(stmt.var)
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
            auto& __iter_N = <iterable>;   // variable — reference for consumption
            auto  __iter_N = <iterable>;   // temporary — copy/move for ownership
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
        inner_indent = indent + "  "
        if elem_type.is_value_type():
            cpp_elem = elem_type.to_cpp()
            out.write(f"{inner_indent}{cpp_elem} {stmt.var} = *{opt_name};\n")
        else:
            out.write(f"{inner_indent}auto& {stmt.var} = *{opt_name};\n")

        self._gen_loop_body(out, stmt, indent, elem_type)

    def _gen_iter_protocol_loop(self, out: TextIO, stmt: TpyForEach, indent: str,
                                iterable_expr: str, elem_type: TpyType) -> None:
        """Generate a while-loop for types with __iter__() returning an iterator.

        Produces:
            auto& __obj_N = container;              // variable — reference
            auto  __obj_N = Container(args);        // temporary — own it
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

        inner_indent = indent + "  "
        if elem_type.is_value_type():
            cpp_elem = elem_type.to_cpp()
            out.write(f"{inner_indent}{cpp_elem} {stmt.var} = *{opt_name};\n")
        else:
            out.write(f"{inner_indent}auto& {stmt.var} = *{opt_name};\n")

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
        expr = StatementGenerator._unwrap_coerce(expr)
        if isinstance(expr, TpyIntLiteral):
            return True
        if isinstance(expr, TpyUnaryOp) and expr.op == '-' and isinstance(expr.operand, TpyIntLiteral):
            return True
        return False

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
            step_ast = self._unwrap_coerce(range_call.args[2])
            if isinstance(step_ast, TpyIntLiteral):
                if step_ast.value == 0:
                    return False  # zero step panics at runtime — use Range ctor
                elif step_ast.value > 0:
                    step_kind = "literal_pos"
                    step_val = step_ast.value
                else:
                    step_kind = "literal_neg"
                    step_val = step_ast.value
            elif (isinstance(step_ast, TpyUnaryOp) and step_ast.op == '-'
                  and isinstance(step_ast.operand, TpyIntLiteral)):
                val = -step_ast.operand.value
                if val == 0:
                    return False
                step_kind = "literal_neg"
                step_val = val
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

        var = stmt.var
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
                incr = self._gen_step_increment(var, step_cpp, elem_type)
                out.write(f"{indent}for ({cpp_elem} {var} = {start_expr}; "
                          f"{var} < {stop_expr}; "
                          f"{incr}) {{\n")
        elif step_kind == "literal_neg":
            if step_val == -1:
                out.write(f"{indent}for ({cpp_elem} {var} = {start_expr}; "
                          f"{var} > {stop_expr}; --{var}) {{\n")
            else:
                step_cpp = gen_args[2]
                incr = self._gen_step_increment(var, step_cpp, elem_type)
                out.write(f"{indent}for ({cpp_elem} {var} = {start_expr}; "
                          f"{var} > {stop_expr}; "
                          f"{incr}) {{\n")
        else:
            # Variable step — capture, zero-check, ternary condition
            step_cpp = gen_args[2]
            step_temp = f"__step_{n}"
            out.write(f"{indent}{cpp_elem} {step_temp} = {step_cpp};\n")
            step_cpp = step_temp
            out.write(f'{indent}if ({step_cpp} == 0) tpy::tpy_panic("range() arg 3 must not be zero");\n')
            incr = self._gen_step_increment(var, step_cpp, elem_type)
            out.write(f"{indent}for ({cpp_elem} {var} = {start_expr}; "
                      f"{step_cpp} > 0 ? {var} < {stop_expr} : {var} > {stop_expr}; "
                      f"{incr}) {{\n")

        self._gen_loop_body(out, stmt, indent, elem_type)
        return True

    def _gen_step_increment(self, var: str, step: str, elem_type: TpyType) -> str:
        """Generate step increment expression, using checked arithmetic for fixed-width integers."""
        from tpyc.typesys import FixedIntType
        if isinstance(elem_type, FixedIntType):
            return f"{var} = tpy::add_check<{elem_type.to_cpp()}>({var}, {step})"
        return f"{var} += {step}"

    def _gen_for_each(self, out: TextIO, stmt: TpyForEach, indent: str) -> None:
        """Generate a for-each loop over a collection or iterator."""
        from tpyc.modules import get_native_iterator_element_type, get_iter_element_type
        iterable_type = self.types.get_resolved_type(stmt.iterable)

        # Optimize range() calls to C-style counter loops (before protocol checks)
        if isinstance(stmt.iterable, TpyCall) and stmt.iterable.func == "range":
            elem_type = iterable_type.get_element_type()
            if elem_type and self._gen_range_counter_loop(out, stmt, indent, elem_type):
                return

        # Check for OptIterator types — these use while-loop codegen
        iter_elem = get_native_iterator_element_type(iterable_type, registry=self.ctx.analyzer.registry)
        if iter_elem is not None:
            iterable = self.expressions.gen_expr_deref(stmt.iterable)
            self._gen_iterator_loop(out, stmt, indent, iterable, iter_elem)
            return

        # Check for __iter__()-based types (container → separate iterator)
        iter_elem = get_iter_element_type(iterable_type, registry=self.ctx.analyzer.registry)
        if iter_elem is not None:
            iterable = self.expressions.gen_expr_deref(stmt.iterable)
            self._gen_iter_protocol_loop(out, stmt, indent, iterable, iter_elem)
            return

        iterable = self.expressions.gen_expr_deref(stmt.iterable)

        # Determine element type for the loop variable
        # Handle protocol types (e.g., NativeIterable[T])
        if is_protocol_type(iterable_type):
            if iterable_type.name == "NativeIterable" and iterable_type.type_args:
                elem_type = iterable_type.type_args[0]
            else:
                elem_type = None  # Will use auto
        else:
            elem_type = iterable_type.get_element_type()

        # Resolve IntLiteralType to BigInt (Python default for int lists)
        if isinstance(elem_type, IntLiteralType):
            elem_type = BIGINT

        # For strings, wrap in std::string_view for range-based for
        if isinstance(iterable_type, StrType):
            iterable = f"std::string_view({iterable})"

        # Flush any pending temps before for loop header
        self.ctx.temps.flush(out, indent)

        # Generate C++ range-based for loop
        # Non-value element types use auto& (reference into container, not pointer-local)
        if elem_type:
            if not elem_type.is_value_type():
                out.write(f"{indent}for (auto& {stmt.var} : {iterable}) {{\n")
            else:
                cpp_type = elem_type.to_cpp()
                out.write(f"{indent}for ({cpp_type} {stmt.var} : {iterable}) {{\n")
            # Track the loop variable's type for use in body expressions
            self.ctx.var_types[stmt.var] = elem_type
        else:
            # Fallback: use auto
            out.write(f"{indent}for (auto {stmt.var} : {iterable}) {{\n")

        self._gen_loop_body(out, stmt, indent, elem_type)
