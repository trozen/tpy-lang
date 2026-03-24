"""Code generation for generator functions (yield -> state machine structs)."""
from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from typing import TYPE_CHECKING

from ..parse.nodes import (
    TpyFunction, TpyYield, TpyStmt, TpyWhile, TpyForEach, TpyReturn, TpyVarDecl,
    TpyCall, TpyName, TpyExpr,
)
from ..typesys import StrType, is_protocol_type
from .context import INDENT, escape_cpp_name


@dataclass
class GeneratorForInfo:
    """Pre-scanned info about a for-loop containing yields in a state machine generator."""
    uid: int
    strategy: str  # "range" | "begin_end" | "next"
    fields: list[tuple[str, str]]  # (field_name, cpp_type_string) for struct fields


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


def _for_body_contains_yield(stmts: list[TpyStmt]) -> bool:
    """Check if a statement list contains any TpyYield (recursive)."""
    for stmt in stmts:
        if isinstance(stmt, TpyYield):
            return True
        for body in stmt.sub_bodies():
            if _for_body_contains_yield(body):
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
            # Container iterable: capture by reference, begin/end iteration.
            # Matches CPython semantics (mutations visible during iteration)
            # and complex generator path (T& struct fields). Borrow analysis
            # protects against dangling references.
            iterable_code = self.expressions.gen_expr(for_stmt.iterable)

            base_captures = self._build_capture_list(func.params, init_stmts)
            iter_type = f"decltype(std::declval<{self.types.type_to_cpp(self.types.get_resolved_type(for_stmt.iterable))}&>().begin())"
            beg_capture = f"__beg = {iter_type}()"
            end_capture = f"__end = {iter_type}()"
            init_flag = "__init = false"
            parts = [p for p in [base_captures, beg_capture, end_capture, init_flag] if p]
            all_captures = ", ".join(parts)

            out.write(f"{ind1}return ::tpy::make_generator<{cpp_elem}>(\n")
            out.write(f"{ind2}[{all_captures}]() mutable -> std::optional<{cpp_elem}> {{\n")
            out.write(f"{ind3}if (!__init) {{ __beg = ({iterable_code}).begin(); __end = ({iterable_code}).end(); __init = true; }}\n")
            out.write(f"{ind3}if (__beg != __end) {{\n")

            self.ctx.indent_level = 4
            if iter_elem and iter_elem.is_value_type():
                out.write(f"{ind4}{cpp_iter_elem} {cpp_var} = *__beg++;\n")
            else:
                out.write(f"{ind4}auto&& {cpp_var} = *__beg++;\n")

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

    def _prescan_for_loops(self, func: TpyFunction) -> dict[int, GeneratorForInfo]:
        """Pre-scan for-loops containing yields to determine struct fields.

        Returns a dict keyed by id(TpyForEach) with field info for each loop.
        """
        from ..sema.diagnostics import SemanticError
        from tpyc.modules import get_error_return_next_element_type

        result: dict[int, GeneratorForInfo] = {}
        uid_counter = [0]

        def _scan(stmts: list[TpyStmt]) -> None:
            for stmt in stmts:
                if isinstance(stmt, TpyForEach) and _for_body_contains_yield(stmt.body):
                    info = self._analyze_for_strategy(stmt, uid_counter[0])
                    if info is None:
                        raise SemanticError(
                            "yield inside this for-loop type is not yet supported "
                            "in generators with multiple yield points. "
                            "Use a while-loop instead",
                            stmt.loc,
                        )
                    result[id(stmt)] = info
                    uid_counter[0] += 1
                    # Recurse into loop body for nested for-loops
                    _scan(stmt.body)
                else:
                    for body in stmt.sub_bodies():
                        _scan(body)

        _scan(func.body)
        return result

    def _analyze_for_strategy(self, stmt: TpyForEach, uid: int) -> GeneratorForInfo | None:
        """Determine the iteration strategy and struct fields for a for-loop with yield."""
        from tpyc.modules import get_error_return_next_element_type
        from ..typesys import IntLiteralType

        elem_type = stmt.elem_type
        if elem_type and isinstance(elem_type, IntLiteralType):
            elem_type = self.ctx.analyzer.ctx.default_int_type
        elem_cpp = self.types.type_to_cpp(elem_type) if elem_type else "int32_t"

        # Range counter optimization
        if isinstance(stmt.iterable, TpyCall) and stmt.iterable.func == "range":
            fields: list[tuple[str, str]] = [
                (f"__for_i_{uid}", elem_cpp),
                (f"__for_stop_{uid}", elem_cpp),
            ]
            nargs = len(stmt.iterable.args)
            if nargs == 3:
                fields.append((f"__for_step_{uid}", elem_cpp))
            return GeneratorForInfo(uid=uid, strategy="range", fields=fields)

        iterable_type = self.types.get_resolved_type(stmt.iterable)

        # Iterator[T] protocol -- iterable already has __next__()
        if is_protocol_type(iterable_type) and iterable_type.qualified_name() == "typing.Iterator":
            result_type = f"std::expected<{elem_cpp}, ::tpy::StopIteration>"
            fields: list[tuple[str, str]] = [(f"__for_r_{uid}", result_type)]
            return GeneratorForInfo(uid=uid, strategy="next", fields=fields)

        # error_return __next__ types (user iterators)
        er_elem = get_error_return_next_element_type(
            iterable_type, registry=self.ctx.analyzer.registry)
        if er_elem is not None:
            iter_cpp = self.types.type_to_cpp(iterable_type)
            result_type = f"std::expected<{elem_cpp}, ::tpy::StopIteration>"
            fields = []
            if not self._is_named_generator_field(stmt.iterable):
                fields.append((f"__for_src_{uid}", iter_cpp))
            fields.append((f"__for_r_{uid}", result_type))
            return GeneratorForInfo(uid=uid, strategy="next", fields=fields)

        # NativeIterable containers (list, dict, set, Array, Span, str) -- begin/end
        native_elem = iterable_type.get_iteration_element_type() if hasattr(iterable_type, 'get_iteration_element_type') else None
        if native_elem is not None:
            container_cpp = self.types.type_to_cpp(iterable_type)
            iter_type = f"decltype(std::declval<{container_cpp}&>().begin())"
            fields = [
                (f"__for_it_{uid}", iter_type),
                (f"__for_end_{uid}", iter_type),
            ]
            # If iterable is not a named variable (param or local), need a source field
            if not self._is_named_generator_field(stmt.iterable):
                fields.insert(0, (f"__for_src_{uid}", container_cpp))
            return GeneratorForInfo(uid=uid, strategy="begin_end", fields=fields)

        # __iter__()-based user types: call __iter__(), then use the result
        from tpyc.modules import get_iter_info
        iter_info = get_iter_info(iterable_type, registry=self.ctx.analyzer.registry)
        if iter_info is not None:
            iter_ret_type = self._get_iter_return_type(iterable_type)
            if iter_ret_type is not None:
                iter_ret_cpp = self.types.type_to_cpp(iter_ret_type)
                result_type = f"std::expected<{elem_cpp}, ::tpy::StopIteration>"
                if iter_info.iter_is_native:
                    # __iter__() returns a NativeIterable (e.g. SpanIter) -- begin/end
                    it_type = f"decltype(std::declval<{iter_ret_cpp}&>().begin())"
                    fields = [
                        (f"__for_itr_{uid}", iter_ret_cpp),
                        (f"__for_it_{uid}", it_type),
                        (f"__for_end_{uid}", it_type),
                    ]
                    return GeneratorForInfo(uid=uid, strategy="iter_begin_end", fields=fields)
                else:
                    # __iter__() returns an iterator with __next__()
                    fields = [
                        (f"__for_itr_{uid}", iter_ret_cpp),
                        (f"__for_r_{uid}", result_type),
                    ]
                    return GeneratorForInfo(uid=uid, strategy="iter_next", fields=fields)

        # Unsupported for-loop type
        return None

    def _is_named_generator_field(self, expr: TpyExpr) -> bool:
        """Check if expression is a named variable that's stable across yields.

        Returns True for named variables (params, locals, globals) since they
        persist across __next__() calls. Returns False for expressions (calls,
        constructors) that would need to be stored in a synthetic field.
        """
        return isinstance(expr, TpyName)

    def _get_iter_return_type(self, iterable_type: 'TpyType') -> 'TpyType | None':
        """Get the concrete return type of __iter__() on the given type."""
        from ..typesys import NamedType, OwnType, TypeParamRef

        registry = self.ctx.analyzer.registry
        record = None
        type_subst: dict[str, 'TpyType'] = {}

        if isinstance(iterable_type, NamedType) and iterable_type.is_user_record:
            record = registry.get_record(iterable_type.name)
        else:
            record = registry.get_record_for_type(iterable_type)

        if record is None:
            return None

        if record.type_params and hasattr(iterable_type, 'type_args') and iterable_type.type_args:
            type_subst = dict(zip(record.type_params, iterable_type.type_args))

        # Walk parent chain for __iter__
        while record is not None:
            method = record.get_method("__iter__")
            if method is not None:
                ret = method.return_type
                if isinstance(ret, TypeParamRef) and ret.name in type_subst:
                    ret = type_subst[ret.name]
                if isinstance(ret, OwnType):
                    ret = ret.wrapped
                return ret
            # Check parent
            if record.parent_name:
                record = registry.get_record(record.parent_name)
            else:
                break
        return None

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
    def _build_capture_list(params: list, init_stmts: list[TpyStmt],
                            exclude: set[str] | None = None) -> str:
        """Build lambda capture list from params + init-stmt locals.

        Non-value types are captured by reference (&name) to preserve
        Python's reference semantics. Names in `exclude` are skipped
        (e.g. the iterable param when it's copied into __src).
        """
        captures = []
        for pname, ptype in params:
            if exclude and pname in exclude:
                continue
            cpp_name = escape_cpp_name(pname)
            if not ptype.is_value_type():
                captures.append(f"&{cpp_name}")
            else:
                captures.append(cpp_name)
        for stmt in init_stmts:
            if isinstance(stmt, TpyVarDecl):
                captures.append(escape_cpp_name(stmt.name))
        return ", ".join(captures)

    def gen_generator_struct(self, out: TextIO, func: TpyFunction) -> None:
        """Generate the state machine struct for a generator function."""
        # Pre-scan for-loops with yields to determine synthetic struct fields
        for_loop_info = self._prescan_for_loops(func)
        self.ctx.generator_for_loop_info = for_loop_info

        struct_name = f"__gen_{func.name}"
        elem_type = func.generator_yield_type
        assert elem_type is not None
        cpp_elem = self.types.type_to_cpp(elem_type)

        # Collect yield state numbers for THIS function
        yield_states = self.ctx.analyzer.ctx.generator_yield_states
        func_yields = _collect_yield_stmts(func.body)
        func_state_nums = sorted(yield_states[id(y)] for y in func_yields)

        # Classify params: value types by value, non-value types by reference.
        # str params use string_view (the param type) -- the generator borrows
        # from the caller's string, same as container refs. No hidden copy.
        ctor_params: list[tuple[str, str, bool]] = []  # (cpp_name, cpp_type, is_ref)
        for pname, ptype in func.params:
            cpp_name = escape_cpp_name(pname)
            if isinstance(ptype, StrType):
                ctor_params.append((cpp_name, "std::string_view", False))
            else:
                cpp_type = self.types.type_to_cpp(ptype)
                is_ref = not ptype.is_value_type()
                ctor_params.append((cpp_name, cpp_type, is_ref))

        out.write(f"// Generator: {func.name}\n")
        out.write(f"struct {struct_name} {{\n")

        # __state field
        out.write(f"{INDENT}int __state;\n")

        # Parameter fields
        for cpp_name, cpp_type, is_ref in ctor_params:
            if is_ref:
                out.write(f"{INDENT}{cpp_type}& {cpp_name};\n")
            else:
                out.write(f"{INDENT}{cpp_type} {cpp_name};\n")

        # Local variable fields
        if func.generator_locals:
            for lname, ltype in func.generator_locals:
                cpp_type = self.types.type_to_cpp(ltype)
                cpp_name = escape_cpp_name(lname)
                if ltype.is_value_type():
                    # Value types: leave uninitialized
                    out.write(f"{INDENT}{cpp_type} {cpp_name};\n")
                else:
                    # Non-value types: std::optional (no premature construction)
                    out.write(f"{INDENT}std::optional<{cpp_type}> {cpp_name};\n")

        # Synthetic fields for for-loops containing yields (always optional)
        if for_loop_info:
            for info in for_loop_info.values():
                for field_name, field_type in info.fields:
                    out.write(f"{INDENT}std::optional<{field_type}> {field_name};\n")

        out.write(f"\n")

        # Constructor: initializes __state and reference params
        ctor_param_list = ", ".join(
            f"{cpp_type}& {cpp_name}" if is_ref else f"{cpp_type} {cpp_name}_"
            for cpp_name, cpp_type, is_ref in ctor_params
        )
        init_parts = ["__state(0)"]
        for cpp_name, _, is_ref in ctor_params:
            if is_ref:
                init_parts.append(f"{cpp_name}({cpp_name})")
            else:
                init_parts.append(f"{cpp_name}({cpp_name}_)")
        init_list = ", ".join(init_parts)
        out.write(f"{INDENT}{struct_name}({ctor_param_list})\n")
        out.write(f"{INDENT}{INDENT}: {init_list} {{}}\n\n")

        # __iter__() method (inline -- trivial)
        out.write(f"{INDENT}{struct_name}& __iter__() {{ return *this; }}\n")
        # __next__() declaration (body in .cpp)
        out.write(f"{INDENT}std::expected<{cpp_elem}, ::tpy::StopIteration> __next__();\n\n")

        # operator<< for printing
        out.write(f"{INDENT}friend std::ostream& operator<<(std::ostream& os, const {struct_name}&) {{\n")
        out.write(f"{INDENT}{INDENT}return os << \"<generator {func.name}>\";\n")
        out.write(f"{INDENT}}}\n")

        out.write(f"}};\n")

    def gen_generator_next(self, out: TextIO, func: TpyFunction) -> None:
        """Generate the out-of-line __next__() method body in the .cpp file."""
        struct_name = f"__gen_{func.name}"
        elem_type = func.generator_yield_type
        assert elem_type is not None
        cpp_elem = self.types.type_to_cpp(elem_type)

        # Re-run prescan (needed for body codegen context)
        for_loop_info = self._prescan_for_loops(func)
        self.ctx.generator_for_loop_info = for_loop_info

        # Collect yield state numbers for THIS function
        yield_states = self.ctx.analyzer.ctx.generator_yield_states
        func_yields = _collect_yield_stmts(func.body)
        func_state_nums = sorted(yield_states[id(y)] for y in func_yields)

        out.write(f"std::expected<{cpp_elem}, ::tpy::StopIteration> {struct_name}::__next__() {{\n")
        inner = INDENT

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
        out.write(f"}}\n")

    def _gen_generator_body(self, out: TextIO, func: TpyFunction, indent: str) -> None:
        """Generate the function body inside __next__() with generator context."""
        # Save and set generator context
        old_in_gen = self.ctx.in_generator_body
        old_field_names = self.ctx.generator_field_names
        old_optional_fields = self.ctx.generator_optional_fields
        old_for_info = self.ctx.generator_for_loop_info

        self.ctx.in_generator_body = True
        self.ctx.generator_field_names = set()
        self.ctx.generator_optional_fields = set()
        for pname, _ in func.params:
            self.ctx.generator_field_names.add(pname)
        if func.generator_locals:
            for lname, ltype in func.generator_locals:
                self.ctx.generator_field_names.add(lname)
                if not ltype.is_value_type():
                    self.ctx.generator_optional_fields.add(lname)
        # Add synthetic for-loop field names (all optional)
        for info in self.ctx.generator_for_loop_info.values():
            for field_name, _ in info.fields:
                self.ctx.generator_field_names.add(field_name)
                self.ctx.generator_optional_fields.add(field_name)

        # Set up codegen context for body generation
        from ..namespace import Namespace
        local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
        for pname, ptype in func.params:
            local_ns.bind_variable(pname, ptype)

        self.statements.gen_body(
            out, func.body, func.params, func.generator_yield_type,
            func, local_ns, indent_level=1, is_method=False,
        )

        # Restore context
        self.ctx.in_generator_body = old_in_gen
        self.ctx.generator_field_names = old_field_names
        self.ctx.generator_optional_fields = old_optional_fields
        self.ctx.generator_for_loop_info = old_for_info

    def gen_generator_factory(self, out: TextIO, func: TpyFunction) -> None:
        """Generate the factory function that creates a generator struct."""
        struct_name = f"__gen_{func.name}"
        params = self.functions.gen_params(
            func.params, func,
            emit_defaults=False,
        )
        out.write(f"{struct_name} {func.name}({params}) {{\n")

        if func.params:
            args = ", ".join(escape_cpp_name(pname) for pname, _ in func.params)
            out.write(f"{INDENT}return {struct_name}({args});\n")
        else:
            out.write(f"{INDENT}return {struct_name}();\n")

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
