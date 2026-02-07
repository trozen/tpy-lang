"""
TurboPython Statement Code Generation

Generates C++ code from TurboPython statements.
"""

from __future__ import annotations
from typing import TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, FloatType,
    ArrayType, ListType, PendingListType, OwnType, NamedType, StrType,
    INT32, BIGINT, is_protocol_type,
)
from ..parse import (
    TpyStmt, TpyVarDecl, TpyAssign, TpyAugAssign, TpyExprStmt, TpyReturn,
    TpyIf, TpyWhile, TpyFor, TpyForEach, TpyBreak, TpyContinue, TpyPassStmt,
    TpyImport, TpySubscript, TpyStrLiteral, TpyName
)
from ..namespace import Namespace
from .context import CodeGenError, module_to_cpp_namespace, expand_cpp_template

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
        elif isinstance(stmt, TpyFor):
            self._gen_for(out, stmt, indent)
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
                return f"{indent}return {self.expressions.gen_expr(stmt.value, ret_type)};\n"
            return f"{indent}return;\n"
        elif isinstance(stmt, TpyBreak):
            return f"{indent}break;\n"
        elif isinstance(stmt, TpyContinue):
            return f"{indent}continue;\n"
        elif isinstance(stmt, TpyPassStmt):
            return ""  # No-op - emit nothing
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
                    if parent_pkg in self.ctx.all_user_modules:
                        result += f"{indent}{module_to_cpp_namespace(parent_pkg)}::__tpy_init();\n"
                # Then init the submodule itself
                result += f"{indent}{module_to_cpp_namespace(stmt.module_name)}::__tpy_init();\n"
                return result
            return ""  # Builtin module - no init needed
        return None

    def _gen_var_decl_code(self, stmt: TpyVarDecl, indent: str) -> str | None:
        """Generate code for a variable declaration. Returns code to write or None."""
        # Check if variable is already declared (reassignment)
        if stmt.name in self.ctx.declared_vars:
            if stmt.init:
                # For reassignment, use the existing variable's type as target
                var_type = self.ctx.var_types.get(stmt.name)
                init_expr = self.expressions.gen_expr(stmt.init, var_type)
                return f"{indent}{stmt.name} = {init_expr};\n"
            return None

        # Determine target type for first declaration
        target_type = stmt.type
        if target_type is None and stmt.init:
            # Check if analyzer resolved the type based on usage
            resolved_type = self.ctx.analyzer.var_types.get(id(stmt))
            if resolved_type:
                target_type = resolved_type
            else:
                target_type = self.ctx.analyzer.get_expr_type(stmt.init)
                # Unwrap OwnType - it indicates ownership transfer, not variable type
                if isinstance(target_type, OwnType):
                    target_type = target_type.wrapped
                # Resolve IntLiteralType to BigInt for standalone variable declarations
                if isinstance(target_type, IntLiteralType):
                    target_type = BIGINT
                # Resolve Array[IntLiteralType] to Array[BigInt]
                elif isinstance(target_type, ArrayType) and isinstance(target_type.element_type, IntLiteralType):
                    target_type = ArrayType(BIGINT, target_type.size)

        # First declaration - track the type and mark as local (shadows globals)
        self.ctx.declared_vars.add(stmt.name)
        self.ctx.local_scope_names.add(stmt.name)
        self.ctx.var_types[stmt.name] = target_type
        # Bind to namespace for global tracking
        if self.ctx.current_ns and target_type:
            self.ctx.current_ns.bind_variable(stmt.name, target_type)

        if stmt.type:
            # Protocol types use auto in generated code (the actual type is the template param)
            if self.ctx.contains_protocol_type(stmt.type):
                cpp_type = "auto"
            else:
                cpp_type = self.types.type_to_cpp(stmt.type)
        elif stmt.init:
            # Check if analyzer resolved the type based on usage
            resolved_type = self.ctx.analyzer.var_types.get(id(stmt))
            if resolved_type:
                cpp_type = self.types.type_to_cpp(resolved_type)
            else:
                # Use inferred type from expression
                inferred_type = self.ctx.analyzer.get_expr_type(stmt.init)
                if inferred_type is None:
                    raise CodeGenError(
                        f"Could not infer type for variable '{stmt.name}'", loc=stmt.loc
                    )
                # Resolve IntLiteralType to BigInt (Python semantics)
                if isinstance(inferred_type, IntLiteralType):
                    inferred_type = BIGINT
                # Resolve container[IntLiteralType] to container[BigInt]
                elif isinstance(inferred_type, (ArrayType, ListType, PendingListType)):
                    elem = getattr(inferred_type, 'element_type', None)
                    if isinstance(elem, IntLiteralType):
                        if isinstance(inferred_type, ArrayType):
                            inferred_type = ArrayType(BIGINT, inferred_type.size)
                        else:
                            inferred_type = ListType(BIGINT)
                # Protocol types use auto (the actual type is the template param)
                if self.ctx.contains_protocol_type(inferred_type):
                    cpp_type = "auto"
                else:
                    cpp_type = self.types.type_to_cpp(inferred_type)
        else:
            raise CodeGenError(f"Variable '{stmt.name}' has no type annotation and no initializer", loc=stmt.loc)

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
            value = self.expressions.gen_expr(stmt.value)
            obj_type = self.ctx.analyzer.get_expr_type(stmt.target.obj)
            index_type = self.ctx.analyzer.get_expr_type(stmt.target.index)
            # Dereference globals for subscript access
            subscript_obj = f"(*{obj})" if self.ctx.is_global_name(stmt.target.obj) else obj
            index_expr = self.expressions.gen_index_expr(subscript_obj, stmt.target.index, index_type)

            # Use registry lookup for __setitem__
            cpp_template = self.builtins.get_type_method_template(obj_type, "__setitem__")
            if cpp_template:
                code = expand_cpp_template(cpp_template, subscript_obj, index_expr, value)
                return f"{indent}{code};\n"
            else:
                return f"{indent}{subscript_obj}[{index_expr}] = {value};\n"

        # Default: simple assignment
        target = self.expressions.gen_expr(stmt.target)
        target_type = self.ctx.analyzer.get_expr_type(stmt.target)
        value = self.expressions.gen_expr(stmt.value, target_type)
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

        # Special case: Int32 += BigInt should convert BigInt to Int32, then use Int32 ops
        # This preserves checked arithmetic and avoids unnecessary promotion to BigInt
        if isinstance(target_type, Int32Type) and isinstance(value_type, BigIntType):
            # Dereference globals before .to_int32() conversion
            if self.ctx.is_global_name(stmt.value):
                value = f"(*{value})"
            value = f"({value}).to_int32()"
            value_type = INT32

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
        subscript_obj = f"(*{obj})" if self.ctx.is_global_name(subscript.obj) else obj
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

        # Special case: Int32 += BigInt should convert BigInt to Int32
        if isinstance(elem_type, Int32Type) and isinstance(value_type, BigIntType):
            # Dereference globals before .to_int32() conversion
            if self.ctx.is_global_name(stmt.value):
                value = f"(*{value})"
            value = f"({value}).to_int32()"
            value_type = INT32

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
        cond = self.expressions.gen_expr(stmt.condition)
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
        cond = self.expressions.gen_expr(stmt.condition)
        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}while ({cond}) {{\n")

        self.ctx.indent_level += 1
        for s in stmt.body:
            self.gen_stmt(out, s)
        self.ctx.indent_level -= 1

        out.write(f"{indent}}}\n")

    def _gen_for(self, out: TextIO, stmt: TpyFor, indent: str) -> None:
        """Generate a for loop (range-based)."""
        start = self.expressions.gen_expr_deref(stmt.start)
        end = self.expressions.gen_expr_deref(stmt.end)
        # Convert BigInt bounds to Int32 (range loops use Int32 counter)
        start_type = self.ctx.analyzer.get_expr_type(stmt.start)
        end_type = self.ctx.analyzer.get_expr_type(stmt.end)
        if isinstance(start_type, BigIntType):
            start = f"{start}.to_int32()"
        if isinstance(end_type, BigIntType):
            end = f"{end}.to_int32()"
        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}for (int32_t {stmt.var} = {start}; {stmt.var} < {end}; ++{stmt.var}) {{\n")

        # Track loop variable as local to prevent false global deref if it shadows a global
        self.ctx.local_scope_names.add(stmt.var)
        # Set up inner namespace for loop variable
        old_ns = self.ctx.current_ns
        if self.ctx.current_ns:
            inner_ns = Namespace(parent=self.ctx.current_ns)
            inner_ns.bind_variable(stmt.var, INT32)
            self.ctx.current_ns = inner_ns
        self.ctx.indent_level += 1
        for s in stmt.body:
            self.gen_stmt(out, s)
        self.ctx.indent_level -= 1
        self.ctx.local_scope_names.discard(stmt.var)
        self.ctx.current_ns = old_ns

        out.write(f"{indent}}}\n")

    def _gen_for_each(self, out: TextIO, stmt: TpyForEach, indent: str) -> None:
        """Generate a for-each loop over a collection."""
        iterable = self.expressions.gen_expr_deref(stmt.iterable)
        iterable_type = self.types.get_resolved_type(stmt.iterable)

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
        if elem_type:
            cpp_type = elem_type.to_cpp()
            out.write(f"{indent}for ({cpp_type} {stmt.var} : {iterable}) {{\n")
            # Track the loop variable's type for use in body expressions
            self.ctx.var_types[stmt.var] = elem_type
        else:
            # Fallback: use auto
            out.write(f"{indent}for (auto {stmt.var} : {iterable}) {{\n")

        # Track loop variable as local to prevent false global deref if it shadows a global
        self.ctx.local_scope_names.add(stmt.var)
        # Set up inner namespace for loop variable
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

        # Remove loop variable type after loop ends
        if stmt.var in self.ctx.var_types:
            del self.ctx.var_types[stmt.var]
