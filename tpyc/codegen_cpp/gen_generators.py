"""Code generation for generator functions (yield -> state machine structs)."""
from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

from ..parse.nodes import (
    TpyFunction, TpyYield, TpyStmt, TpyWhile, TpyForEach, TpyReturn, TpyVarDecl,
    TpyCall,
)
from .context import INDENT, escape_cpp_name


def _collect_yield_stmts(stmts: list[TpyStmt]) -> list[TpyYield]:
    """Collect all TpyYield statements from a body (recursive, no nested defs)."""
    result: list[TpyYield] = []
    for stmt in stmts:
        if isinstance(stmt, TpyYield):
            result.append(stmt)
        for body in stmt.sub_bodies():
            result.extend(_collect_yield_stmts(body))
    return result


def _contains_return(stmts: list[TpyStmt]) -> bool:
    """Check if any TpyReturn exists in the statement list (recursive)."""
    for stmt in stmts:
        if isinstance(stmt, TpyReturn):
            return True
        for body in stmt.sub_bodies():
            if _contains_return(body):
                return True
    return False

if TYPE_CHECKING:
    from io import TextIO
    from .context import CodeGenContext
    from .types import TypeMapper
    from .expressions import ExpressionGenerator
    from .statements import StatementGenerator
    from .functions import FunctionGenerator


class GeneratorCodegen:
    """Generates C++ code for generator functions as state machine structs."""

    def __init__(
        self,
        ctx: CodeGenContext,
        types: TypeMapper,
        expressions: ExpressionGenerator,
        statements: StatementGenerator,
        functions: FunctionGenerator,
    ):
        self.ctx = ctx
        self.types = types
        self.expressions = expressions
        self.statements = statements
        self.functions = functions

    def is_simple_generator(self, func: TpyFunction) -> bool:
        """Check if a generator can use the lightweight lambda/wrapper path.

        Simple generators have exactly one yield inside a single while-loop or
        for-loop, with no early returns or nested control flow around the yield.
        """
        yields = _collect_yield_stmts(func.body)
        if len(yields) != 1:
            return False
        if _contains_return(func.body):
            return False
        if not func.body:
            return False
        last = func.body[-1]
        if isinstance(last, TpyWhile) and not last.orelse:
            loop_body = last.body
        elif isinstance(last, TpyForEach) and not last.orelse and not last.is_tuple_unpack:
            loop_body = last.body
        else:
            return False
        # The single yield must be a direct child of the loop body
        for stmt in loop_body:
            if isinstance(stmt, TpyYield):
                return True
        return False

    def gen_simple_generator_inline(self, out: TextIO, func: TpyFunction) -> None:
        """Generate a simple generator as an inline function using make_generator + lambda."""
        last = func.body[-1]
        if isinstance(last, TpyForEach):
            self._gen_simple_for_generator(out, func)
        else:
            self._gen_simple_while_generator(out, func)

    def _gen_simple_while_generator(self, out: TextIO, func: TpyFunction) -> None:
        """Lambda codegen for: [init] while(cond): ... yield expr ..."""
        elem_type = func.generator_yield_type
        assert elem_type is not None
        cpp_elem = self.types.type_to_cpp(elem_type)

        while_stmt = func.body[-1]
        assert isinstance(while_stmt, TpyWhile)
        init_stmts = func.body[:-1]
        yield_stmt, pre_yield, post_yield = self._split_at_yield(while_stmt.body)

        ind1 = INDENT
        ind2 = INDENT * 2
        ind3 = INDENT * 3
        ind4 = INDENT * 4

        params = self.functions.gen_params(func.params, func, emit_defaults=True)
        out.write(f"inline auto {escape_cpp_name(func.name)}({params}) {{\n")

        # Generate init stmts and set up codegen scope
        self._setup_body_scope(out, func, init_stmts)

        captures = self._build_capture_list(func.params, init_stmts)
        out.write(f"{ind1}return ::tpy::make_generator<{cpp_elem}>(\n")
        out.write(f"{ind2}[{captures}]() mutable -> std::optional<{cpp_elem}> {{\n")

        old_indent = self.ctx.indent_level
        self.ctx.indent_level = 3
        cond_code = self.expressions.gen_expr(while_stmt.condition)
        out.write(f"{ind3}while ({cond_code}) {{\n")
        self.ctx.indent_level = 4

        for stmt in pre_yield:
            self.statements.gen_stmt(out, stmt)
        yield_expr = self.expressions.gen_expr(yield_stmt.value)
        out.write(f"{ind4}auto __val = {yield_expr};\n")
        for stmt in post_yield:
            self.statements.gen_stmt(out, stmt)

        out.write(f"{ind4}return std::optional<{cpp_elem}>(__val);\n")
        out.write(f"{ind3}}}\n")
        out.write(f"{ind3}return std::nullopt;\n")
        out.write(f"{ind2}}}\n")
        out.write(f"{ind1});\n")
        out.write(f"}}\n")
        self.ctx.indent_level = old_indent

    def _gen_simple_for_generator(self, out: TextIO, func: TpyFunction) -> None:
        """Lambda codegen for: [init] for x in iterable: ... yield expr ..."""
        from .context import is_lvalue_iterable
        from ..typesys import IntLiteralType, TupleType

        elem_type = func.generator_yield_type
        assert elem_type is not None
        cpp_elem = self.types.type_to_cpp(elem_type)

        for_stmt = func.body[-1]
        assert isinstance(for_stmt, TpyForEach)
        init_stmts = func.body[:-1]
        yield_stmt, pre_yield, post_yield = self._split_at_yield(for_stmt.body)

        ind1 = INDENT
        ind2 = INDENT * 2
        ind3 = INDENT * 3
        ind4 = INDENT * 4

        params = self.functions.gen_params(func.params, func, emit_defaults=True)
        out.write(f"inline auto {escape_cpp_name(func.name)}({params}) {{\n")

        self._setup_body_scope(out, func, init_stmts)

        old_indent = self.ctx.indent_level
        self.ctx.indent_level = 2

        # Determine iteration strategy
        is_range = isinstance(for_stmt.iterable, TpyCall) and for_stmt.iterable.func == "range"
        iter_elem = for_stmt.elem_type
        if iter_elem and isinstance(iter_elem, IntLiteralType):
            iter_elem = self.ctx.analyzer.ctx.default_int_type
        cpp_iter_elem = self.types.type_to_cpp(iter_elem) if iter_elem else "auto"
        cpp_var = escape_cpp_name(for_stmt.var)

        if is_range and len(for_stmt.iterable.args) <= 2:
            # range(n) or range(start, stop): counter in lambda captures
            range_call = for_stmt.iterable
            nargs = len(range_call.args)
            if nargs == 1:
                stop_code = self.expressions.gen_expr(range_call.args[0])
                extra_captures = f"__i = {cpp_iter_elem}(0), __stop = static_cast<{cpp_iter_elem}>({stop_code})"
            else:
                start_code = self.expressions.gen_expr(range_call.args[0])
                stop_code = self.expressions.gen_expr(range_call.args[1])
                extra_captures = f"__i = static_cast<{cpp_iter_elem}>({start_code}), __stop = static_cast<{cpp_iter_elem}>({stop_code})"

            base_captures = self._build_capture_list(func.params, init_stmts)
            all_captures = f"{base_captures}, {extra_captures}" if base_captures else extra_captures

            out.write(f"{ind1}return ::tpy::make_generator<{cpp_elem}>(\n")
            out.write(f"{ind2}[{all_captures}]() mutable -> std::optional<{cpp_elem}> {{\n")
            out.write(f"{ind3}while (__i < __stop) {{\n")

            self.ctx.indent_level = 4
            out.write(f"{ind4}{cpp_iter_elem} {cpp_var} = __i++;\n")
            for stmt in pre_yield:
                self.statements.gen_stmt(out, stmt)
            yield_expr = self.expressions.gen_expr(yield_stmt.value)
            out.write(f"{ind4}auto __val = {yield_expr};\n")
            for stmt in post_yield:
                self.statements.gen_stmt(out, stmt)
            out.write(f"{ind4}return std::optional<{cpp_elem}>(__val);\n")
            out.write(f"{ind3}}}\n")
            out.write(f"{ind3}return std::nullopt;\n")
            out.write(f"{ind2}}}\n")
            out.write(f"{ind1});\n")
            out.write(f"}}\n")
        else:
            # Container iterable: copy into lambda, index-based iteration.
            # Avoids dangling iterators when generator outlives the source.
            iterable_code = self.expressions.gen_expr(for_stmt.iterable)

            base_captures = self._build_capture_list(func.params, init_stmts)
            src_capture = f"__src = {self.types.type_to_cpp(self.types.get_resolved_type(for_stmt.iterable))}({iterable_code})"
            idx_capture = "__i = size_t(0)"
            parts = [p for p in [base_captures, src_capture, idx_capture] if p]
            all_captures = ", ".join(parts)

            out.write(f"{ind1}return ::tpy::make_generator<{cpp_elem}>(\n")
            out.write(f"{ind2}[{all_captures}]() mutable -> std::optional<{cpp_elem}> {{\n")
            out.write(f"{ind3}if (__i < __src.size()) {{\n")

            self.ctx.indent_level = 4
            if iter_elem and iter_elem.is_value_type():
                out.write(f"{ind4}{cpp_iter_elem} {cpp_var} = __src[__i++];\n")
            else:
                out.write(f"{ind4}auto&& {cpp_var} = __src[__i++];\n")

            for stmt in pre_yield:
                self.statements.gen_stmt(out, stmt)
            yield_expr = self.expressions.gen_expr(yield_stmt.value)
            out.write(f"{ind4}auto __val = {yield_expr};\n")
            for stmt in post_yield:
                self.statements.gen_stmt(out, stmt)
            out.write(f"{ind4}return std::optional<{cpp_elem}>(__val);\n")
            out.write(f"{ind3}}}\n")
            out.write(f"{ind3}return std::nullopt;\n")
            out.write(f"{ind2}}}\n")
            out.write(f"{ind1});\n")
            out.write(f"}}\n")

        self.ctx.indent_level = old_indent

    @staticmethod
    def _split_at_yield(body: list[TpyStmt]) -> tuple[TpyYield, list[TpyStmt], list[TpyStmt]]:
        """Split a loop body at the yield statement. Returns (yield, pre, post)."""
        for i, stmt in enumerate(body):
            if isinstance(stmt, TpyYield):
                return stmt, body[:i], body[i + 1:]
        raise AssertionError("no yield found in body")

    @staticmethod
    def _check_no_yield_in_for_loop(func: TpyFunction) -> None:
        """Reject yield inside for-loops in complex (state machine) generators."""
        from ..sema.diagnostics import SemanticError

        def _check(stmts: list[TpyStmt], in_for: bool) -> None:
            for stmt in stmts:
                if isinstance(stmt, TpyYield) and in_for:
                    raise SemanticError(
                        "yield inside for-loops is not yet supported in generators "
                        "with multiple yield points or early returns. "
                        "Use a while-loop instead, or restructure as a single-yield generator",
                        stmt.loc,
                    )
                if isinstance(stmt, TpyForEach):
                    _check(stmt.body, in_for=True)
                    if stmt.orelse:
                        _check(stmt.orelse, in_for)
                else:
                    for body in stmt.sub_bodies():
                        _check(body, in_for)

        _check(func.body, in_for=False)

    def _setup_body_scope(self, out: TextIO, func: TpyFunction, init_stmts: list[TpyStmt]) -> None:
        """Generate init stmts and set up codegen scope."""
        from ..namespace import Namespace
        local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
        for pname, ptype in func.params:
            local_ns.bind_variable(pname, ptype)
        self.statements.gen_body(
            out, init_stmts, func.params, func.generator_yield_type,
            func, local_ns, indent_level=1, is_method=False,
        )

    @staticmethod
    def _build_capture_list(params: list, init_stmts: list[TpyStmt]) -> str:
        """Build lambda capture list from params + init-stmt locals."""
        captures = []
        for pname, _ in params:
            captures.append(escape_cpp_name(pname))
        for stmt in init_stmts:
            if isinstance(stmt, TpyVarDecl):
                captures.append(escape_cpp_name(stmt.name))
        return ", ".join(captures)

    def gen_generator_struct(self, out: TextIO, func: TpyFunction) -> None:
        """Generate the state machine struct for a generator function."""
        # Check for unsupported yield-inside-for-loop in complex generators
        self._check_no_yield_in_for_loop(func)

        struct_name = f"__gen_{func.name}"
        elem_type = func.generator_yield_type
        assert elem_type is not None
        cpp_elem = self.types.type_to_cpp(elem_type)

        # Collect yield state numbers for THIS function
        yield_states = self.ctx.analyzer.ctx.generator_yield_states
        func_yields = _collect_yield_stmts(func.body)
        func_state_nums = sorted(yield_states[id(y)] for y in func_yields)

        out.write(f"// Generator: {func.name}\n")
        out.write(f"struct {struct_name} {{\n")

        # __state field
        out.write(f"{INDENT}int __state = 0;\n")

        # Parameter fields
        for pname, ptype in func.params:
            cpp_type = self.types.type_to_cpp(ptype)
            out.write(f"{INDENT}{cpp_type} {escape_cpp_name(pname)};\n")

        # Local variable fields (value-initialized)
        if func.generator_locals:
            for lname, ltype in func.generator_locals:
                cpp_type = self.types.type_to_cpp(ltype)
                out.write(f"{INDENT}{cpp_type} {escape_cpp_name(lname)}{{}};\n")

        out.write(f"\n")

        # __iter__() method
        out.write(f"{INDENT}{struct_name}& __iter__() {{ return *this; }}\n\n")

        # __next__() method
        out.write(f"{INDENT}std::expected<{cpp_elem}, ::tpy::StopIteration> __next__() {{\n")
        inner = INDENT + INDENT

        # Switch dispatch
        out.write(f"{inner}switch (__state) {{\n")
        out.write(f"{inner}{INDENT}case 0: break;\n")
        for state_num in func_state_nums:
            out.write(f"{inner}{INDENT}case {state_num}: goto __resume_{state_num};\n")
        out.write(f"{inner}{INDENT}default: goto __done;\n")
        out.write(f"{inner}}}\n")

        # Generate the function body with generator context
        self._gen_generator_body(out, func, inner)

        # Exhaustion label
        out.write(f"{inner}__done:\n")
        out.write(f"{inner}__state = -1;\n")
        out.write(f"{inner}return ::tpy::make_unexpected(::tpy::StopIteration{{}});\n")
        out.write(f"{INDENT}}}\n\n")

        # operator<< for printing
        out.write(f"{INDENT}friend std::ostream& operator<<(std::ostream& os, const {struct_name}&) {{\n")
        out.write(f"{INDENT}{INDENT}return os << \"<generator {func.name}>\";\n")
        out.write(f"{INDENT}}}\n")

        out.write(f"}};\n")

    def _gen_generator_body(self, out: TextIO, func: TpyFunction, indent: str) -> None:
        """Generate the function body inside __next__() with generator context."""
        # Save and set generator context
        old_in_gen = self.ctx.in_generator_body
        old_field_names = self.ctx.generator_field_names

        self.ctx.in_generator_body = True
        self.ctx.generator_field_names = set()
        for pname, _ in func.params:
            self.ctx.generator_field_names.add(pname)
        if func.generator_locals:
            for lname, _ in func.generator_locals:
                self.ctx.generator_field_names.add(lname)

        # Set up codegen context for body generation
        from ..namespace import Namespace
        local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
        for pname, ptype in func.params:
            local_ns.bind_variable(pname, ptype)

        self.statements.gen_body(
            out, func.body, func.params, func.generator_yield_type,
            func, local_ns, indent_level=2, is_method=False,
        )

        # Restore context
        self.ctx.in_generator_body = old_in_gen
        self.ctx.generator_field_names = old_field_names

    def gen_generator_factory(self, out: TextIO, func: TpyFunction) -> None:
        """Generate the factory function that creates a generator struct."""
        struct_name = f"__gen_{func.name}"
        params = self.functions.gen_params(
            func.params, func,
            emit_defaults=False,
        )
        out.write(f"{struct_name} {func.name}({params}) {{\n")

        # Construct the struct with parameter values
        if func.params:
            out.write(f"{INDENT}{struct_name} __gen{{}};\n")
            for pname, ptype in func.params:
                cpp_pname = escape_cpp_name(pname)
                out.write(f"{INDENT}__gen.{cpp_pname} = {cpp_pname};\n")
            out.write(f"{INDENT}return __gen;\n")
        else:
            out.write(f"{INDENT}return {struct_name}{{}};\n")

        out.write(f"}}\n")

    def gen_generator_forward_decl(self, out: TextIO, func: TpyFunction) -> bool:
        """Generate forward declarations for a generator function.

        Emits struct forward declaration and factory function forward declaration.
        Returns True if anything was emitted.
        """
        struct_name = f"__gen_{func.name}"
        out.write(f"struct {struct_name};\n")
        return True

    def gen_generator_factory_forward_decl(self, out: TextIO, func: TpyFunction) -> bool:
        """Generate forward declaration for the factory function."""
        struct_name = f"__gen_{func.name}"
        params = self.functions.gen_params(
            func.params, func,
            emit_defaults=True,
        )
        out.write(f"{struct_name} {func.name}({params});\n")
        return True
