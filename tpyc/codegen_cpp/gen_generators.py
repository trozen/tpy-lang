"""Code generation for generator functions (yield -> state machine structs)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

from ..parse.nodes import (
    TpyFunction, TpyYield, TpyStmt, TpyWhile, TpyForEach, TpyReturn, TpyVarDecl,
    TpyCall, TpyName, TpyExpr, TpyTupleUnpack,
)
from ..typesys import IntLiteralType, OptionalType, ReadonlyType, TypeParamRef, TupleType, is_protocol_type, unwrap_readonly, unwrap_ref_type, yield_uses_borrow_slot
from tpyc import modules as builtin_modules
from .context import INDENT, escape_cpp_name
from .protocols import protocol_param_template_name


@dataclass
class GeneratorForInfo:
    """Pre-scanned info about a for-loop containing yields in a state machine generator."""
    uid: int
    strategy: str  # "range" | "begin_end" | "next" | "iter_next"
    fields: list[tuple[str, str]]  # (field_name, cpp_type_string) for struct fields
    # Loop variable name when the iter source yields stable lvalue references
    # (begin_end over a NativeIterable container of non-value elements). The
    # variable's frame slot is emitted as `T*` (pointer-form), not
    # `std::optional<T>` (value-copy), so `for it in items: ... prev = it`
    # preserves CPython aliasing semantics across yield/resume instead of
    # copying the element into the frame. None when copy-storage is used.
    pointer_form_loop_var: str | None = None
    # For a tuple-unpack loop (`for a, b in items:`) whose element tuple
    # contains non-value, non-readonly members, the names of the unpack
    # targets bound to those members. They are stored as `T*` (alias into
    # the live container element via the pointer-form `__for_tup`), so
    # mutating an unpacked record propagates to the source -- matching both
    # CPython and the plain pointer-form loop var above. Value/readonly
    # members are NOT listed (they stay value-copy). Empty for non-unpack
    # loops and all-value tuples.
    pointer_form_unpack_targets: frozenset[str] = frozenset()


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

    def _gen_template_header(self, func: TpyFunction, indent: str = "") -> str:
        """Generate template header for a generic generator function.

        Returns empty string if the function is not generic and has no protocol params.
        """
        proto_params = self.functions.protocols.get_all_protocol_params(func.params)
        return self.functions._gen_template_header_with_fn(func, proto_params, indent=indent)

    def _gen_params(self, func: TpyFunction, *, emit_defaults: bool = False) -> str:
        """Generate parameter list for a generator function, handling protocol params."""
        proto_params = self.functions.protocols.get_all_protocol_params(func.params)
        has_dynamic = self.functions._has_dynamic_protocol_params(func.params)
        if proto_params or has_dynamic:
            return self.functions.gen_params_with_protocols(
                func.params, func.type_params, emit_defaults=emit_defaults)
        return self.functions.gen_params(func.params, func.type_params,
                                         emit_defaults=emit_defaults, func=func)

    @staticmethod
    def gen_struct_name(func: TpyFunction, record_name: str | None = None) -> str:
        """Compute the generator struct name."""
        if record_name:
            return f"__gen_{record_name}_{func.name}"
        return f"__gen_{func.name}"

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

    def gen_simple_generator_inline(self, out: TextIO, func: TpyFunction,
                                    record_name: str | None = None) -> None:
        """Generate a simple generator as an inline function using make_generator + lambda."""
        last = func.body[-1]
        if isinstance(last, TpyForEach):
            self._gen_simple_for_generator(out, func, record_name=record_name)
        else:
            self._gen_simple_while_generator(out, func, record_name=record_name)

    def _gen_simple_while_generator(self, out: TextIO, func: TpyFunction,
                                    record_name: str | None = None) -> None:
        """Lambda codegen for: [init] while(cond): ... yield expr ..."""
        elem_type = func.generator_yield_type
        assert elem_type is not None
        cpp_elem = self.types.type_to_cpp(elem_type)
        cpp_iter_slot = self._iter_slot_for_yield(elem_type, cpp_elem)

        while_stmt = func.body[-1]
        assert isinstance(while_stmt, TpyWhile)
        init_stmts = func.body[:-1]
        yield_stmt, pre_yield, post_yield = self._split_at_yield(while_stmt.body)
        ref_yield = self._yield_binds_by_ref(elem_type, post_yield)
        val_binding = "auto&&" if ref_yield else "auto"

        ind1 = INDENT
        ind2 = INDENT * 2
        ind3 = INDENT * 3
        ind4 = INDENT * 4

        extra = 1 if record_name else 0

        if record_name:
            const_suffix = " const" if func.is_readonly else ""
            out.write(f"\n")
            self.ctx.emit_source_comment(out, func.loc, indent=INDENT)
            tpl_header = self._gen_template_header(func, indent=INDENT)
            if tpl_header:
                out.write(tpl_header)
            params = self._gen_params(func, emit_defaults=True)
            out.write(f"{ind1}auto {func.name}({params}){const_suffix} {{\n")
        else:
            tpl_header = self._gen_template_header(func)
            if tpl_header:
                out.write(tpl_header)
            params = self._gen_params(func, emit_defaults=True)
            out.write(f"inline auto {escape_cpp_name(func.name)}({params}) {{\n")

        # Generate init stmts and set up codegen scope
        old_self_ref = self.ctx.generator_self_ref
        if record_name:
            self.ctx.generator_self_ref = "(*this)"
        self._setup_body_scope(out, func, init_stmts,
                               indent_level=1 + extra)

        captures = self._build_capture_list(func.params, init_stmts)
        if record_name:
            self_capture = "this"
            captures = f"{self_capture}, {captures}" if captures else self_capture
        out.write(f"{INDENT * (1 + extra)}return ::tpy::make_generator<{cpp_iter_slot}>(\n")
        out.write(f"{INDENT * (2 + extra)}[{captures}]() mutable -> std::optional<{cpp_iter_slot}> {{\n")

        old_indent = self.ctx.indent_level
        self.ctx.indent_level = 3 + extra
        cond_code = self.expressions.gen_expr(while_stmt.condition)
        out.write(f"{INDENT * (3 + extra)}while ({cond_code}) {{\n")
        self.ctx.indent_level = 4 + extra

        for stmt in pre_yield:
            self.statements.gen_stmt(out, stmt)
        yield_expr = self.statements.gen_yield_value(yield_stmt)
        out.write(f"{INDENT * (4 + extra)}{val_binding} __val = {yield_expr};\n")
        for stmt in post_yield:
            self.statements.gen_stmt(out, stmt)

        out.write(f"{INDENT * (4 + extra)}return std::optional<{cpp_iter_slot}>(__val);\n")
        out.write(f"{INDENT * (3 + extra)}}}\n")
        out.write(f"{INDENT * (3 + extra)}return std::nullopt;\n")
        out.write(f"{INDENT * (2 + extra)}}}\n")
        out.write(f"{INDENT * (1 + extra)});\n")
        out.write(f"{INDENT if record_name else ''}}}\n")
        self.ctx.indent_level = old_indent
        self.ctx.generator_self_ref = old_self_ref

    def _gen_simple_for_generator(self, out: TextIO, func: TpyFunction,
                                   record_name: str | None = None) -> None:
        """Lambda codegen for: [init] for x in iterable: ... yield expr ..."""
        from .context import is_lvalue_iterable

        elem_type = func.generator_yield_type
        assert elem_type is not None
        cpp_elem = self.types.type_to_cpp(elem_type)
        cpp_iter_slot = self._iter_slot_for_yield(elem_type, cpp_elem)

        for_stmt = func.body[-1]
        assert isinstance(for_stmt, TpyForEach)
        init_stmts = func.body[:-1]
        yield_stmt, pre_yield, post_yield = self._split_at_yield(for_stmt.body)

        ref_yield = self._yield_binds_by_ref(elem_type, post_yield)
        val_binding = "auto&&" if ref_yield else "auto"

        ind1 = INDENT
        ind2 = INDENT * 2
        ind3 = INDENT * 3
        ind4 = INDENT * 4

        extra = 1 if record_name else 0
        if record_name:
            const_suffix = " const" if func.is_readonly else ""
            out.write(f"\n")
            self.ctx.emit_source_comment(out, func.loc, indent=INDENT)
            tpl_header = self._gen_template_header(func, indent=INDENT)
            if tpl_header:
                out.write(tpl_header)
            params = self._gen_params(func, emit_defaults=True)
            out.write(f"{ind1}auto {func.name}({params}){const_suffix} {{\n")
        else:
            tpl_header = self._gen_template_header(func)
            if tpl_header:
                out.write(tpl_header)
            params = self._gen_params(func, emit_defaults=True)
            out.write(f"inline auto {escape_cpp_name(func.name)}({params}) {{\n")

        old_self_ref = self.ctx.generator_self_ref
        if record_name:
            self.ctx.generator_self_ref = "(*this)"
        self._setup_body_scope(out, func, init_stmts,
                               indent_level=1 + extra)

        old_indent = self.ctx.indent_level
        self.ctx.indent_level = 2 + extra

        # Determine iteration strategy
        is_range = isinstance(for_stmt.iterable, TpyCall) and for_stmt.iterable.func_name == "range"
        iter_elem = for_stmt.elem_type
        if iter_elem and isinstance(iter_elem, IntLiteralType):
            iter_elem = self.ctx.analyzer.ctx.default_int_type
        cpp_iter_elem = self.types.type_to_cpp(iter_elem) if iter_elem else "auto"
        cpp_var = escape_cpp_name(for_stmt.var)

        # Helper: prepend self capture for method generators
        def _add_self_capture(captures: str) -> str:
            if record_name:
                return f"this, {captures}" if captures else "this"
            return captures

        # Indentation helpers adjusted for method nesting
        I = lambda n: INDENT * (n + extra)

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
            all_captures = _add_self_capture(all_captures)

            out.write(f"{I(1)}return ::tpy::make_generator<{cpp_iter_slot}>(\n")
            out.write(f"{I(2)}[{all_captures}]() mutable -> std::optional<{cpp_iter_slot}> {{\n")
            out.write(f"{I(3)}while (__i < __stop) {{\n")

            self.ctx.indent_level = 4 + extra
            out.write(f"{I(4)}{cpp_iter_elem} {cpp_var} = __i++;\n")
            for stmt in pre_yield:
                self.statements.gen_stmt(out, stmt)
            yield_expr = self.statements.gen_yield_value(yield_stmt)
            out.write(f"{I(4)}{val_binding} __val = {yield_expr};\n")
            for stmt in post_yield:
                self.statements.gen_stmt(out, stmt)
            out.write(f"{I(4)}return std::optional<{cpp_iter_slot}>(__val);\n")
            out.write(f"{I(3)}}}\n")
            out.write(f"{I(3)}return std::nullopt;\n")
            out.write(f"{I(2)}}}\n")
            out.write(f"{I(1)});\n")
            out.write(f"{I(0)}}}\n")
        elif self._is_direct_iterator(for_stmt):
            # Iterator[T] protocol: the param IS the iterator, call __next__() directly.
            # No __iter__() call needed -- avoids copying move-only iterators.
            iterable_code = self.expressions.gen_expr(for_stmt.iterable)
            base_captures = self._build_capture_list(func.params, init_stmts)
            all_captures = _add_self_capture(base_captures)

            out.write(f"{I(1)}return ::tpy::make_generator<{cpp_iter_slot}>(\n")
            out.write(f"{I(2)}[{all_captures}]() mutable -> std::optional<{cpp_iter_slot}> {{\n")
            out.write(f"{I(3)}auto __r = ({iterable_code}).__next__();\n")
            out.write(f"{I(3)}if (!__r.has_value()) return std::nullopt;\n")

            self._gen_simple_for_yield_body(
                out, for_stmt, pre_yield, post_yield, yield_stmt,
                iter_elem, cpp_iter_elem, cpp_var, cpp_iter_slot, val_binding, I, extra)
        elif self._is_builtin_native_iterable(for_stmt):
            # Built-in NativeIterable (list, dict, set, Span, etc.):
            # begin/end peephole for efficiency.
            iterable_code = self.expressions.gen_expr(for_stmt.iterable)

            base_captures = self._build_capture_list(func.params, init_stmts)
            iter_type = f"decltype(({iterable_code}).begin())"
            beg_capture = f"__beg = {iter_type}()"
            end_capture = f"__end = {iter_type}()"
            init_flag = "__init = false"
            parts = [p for p in [base_captures, beg_capture, end_capture, init_flag] if p]
            all_captures = ", ".join(parts)
            all_captures = _add_self_capture(all_captures)

            out.write(f"{I(1)}return ::tpy::make_generator<{cpp_iter_slot}>(\n")
            out.write(f"{I(2)}[{all_captures}]() mutable -> std::optional<{cpp_iter_slot}> {{\n")
            out.write(f"{I(3)}if (!__init) {{ __beg = ({iterable_code}).begin(); __end = ({iterable_code}).end(); __init = true; }}\n")
            out.write(f"{I(3)}if (__beg != __end) {{\n")

            self.ctx.indent_level = 4 + extra
            if iter_elem and iter_elem.is_value_type():
                out.write(f"{I(4)}{cpp_iter_elem} {cpp_var} = *__beg++;\n")
            else:
                out.write(f"{I(4)}auto&& {cpp_var} = *__beg++;\n")
            if (isinstance(iter_elem, TupleType)
                    and iter_elem.has_pointer_repr_optional_element()):
                self.ctx.storage_form_tuple_locals.add(for_stmt.var)

            for stmt in pre_yield:
                self.statements.gen_stmt(out, stmt)
            yield_expr = self.statements.gen_yield_value(yield_stmt)
            out.write(f"{I(4)}{val_binding} __val = {yield_expr};\n")
            for stmt in post_yield:
                self.statements.gen_stmt(out, stmt)
            out.write(f"{I(4)}return std::optional<{cpp_iter_slot}>(__val);\n")
            out.write(f"{I(3)}}}\n")
            out.write(f"{I(3)}return std::nullopt;\n")
            out.write(f"{I(2)}}}\n")
            out.write(f"{I(1)});\n")
            out.write(f"{I(0)}}}\n")
        else:
            # Universal default: ::tpy::__iter__() + __next__() loop.
            # Handles Iterable[T] protocol, NativeIterable[T] protocol,
            # user types with __iter__(), error_return __next__ iterators.
            iterable_code = self.expressions.gen_expr(for_stmt.iterable)
            base_captures = self._build_capture_list(func.params, init_stmts)

            iter_type = f"std::decay_t<decltype(::tpy::__iter__({iterable_code}))>"
            iter_capture = f"__iter = std::optional<{iter_type}>()"
            parts = [p for p in [base_captures, iter_capture] if p]
            all_captures = ", ".join(parts)
            all_captures = _add_self_capture(all_captures)

            out.write(f"{I(1)}return ::tpy::make_generator<{cpp_iter_slot}>(\n")
            out.write(f"{I(2)}[{all_captures}]() mutable -> std::optional<{cpp_iter_slot}> {{\n")
            out.write(f"{I(3)}if (!__iter) {{ __iter.emplace(::tpy::__iter__({iterable_code})); }}\n")
            out.write(f"{I(3)}auto __r = (*__iter).__next__();\n")
            out.write(f"{I(3)}if (!__r.has_value()) return std::nullopt;\n")

            self._gen_simple_for_yield_body(
                out, for_stmt, pre_yield, post_yield, yield_stmt,
                iter_elem, cpp_iter_elem, cpp_var, cpp_iter_slot, val_binding, I, extra)

        self.ctx.indent_level = old_indent
        self.ctx.generator_self_ref = old_self_ref

    def _resolve_iterable_type(self, for_stmt: TpyForEach):
        """Resolve the iterable's type, following TypeParamRef bounds."""
        resolved = self.types.get_resolved_type(for_stmt.iterable)
        if isinstance(resolved, TypeParamRef):
            bound = self.ctx.current_type_param_bounds.get(resolved.name)
            if bound is not None and is_protocol_type(bound):
                return bound
        return resolved

    def _is_direct_iterator(self, for_stmt: TpyForEach) -> bool:
        """Check if the iterable IS an iterator (has __next__() directly).

        True for Iterator[T] protocol params. These should not go through
        __iter__() to avoid copying move-only iterators.
        """
        resolved = self._resolve_iterable_type(for_stmt)
        return (is_protocol_type(resolved)
                and resolved.qualified_name() == "typing.Iterator")

    def _is_builtin_native_iterable(self, for_stmt: TpyForEach) -> bool:
        """Check if the iterable is a built-in NativeIterable (list, dict, etc.)."""
        resolved = self._resolve_iterable_type(for_stmt)
        record = self.ctx.analyzer.registry.get_record_for_type(resolved)
        return (record is not None and record.is_native
                and builtin_modules.is_native_iterable(resolved, registry=self.ctx.analyzer.registry))

    def _gen_simple_for_yield_body(
        self, out: 'TextIO', for_stmt: TpyForEach,
        pre_yield: list[TpyStmt], post_yield: list[TpyStmt], yield_stmt: TpyYield,
        iter_elem: 'TpyType | None', cpp_iter_elem: str, cpp_var: str,
        cpp_iter_slot: str, val_binding: str, I: 'Callable[[int], str]', extra: int,
    ) -> None:
        """Emit the shared yield body for __iter__+__next__ simple generator branches."""
        self.ctx.indent_level = 3 + extra
        out.write(f"{I(3)}{{\n")
        self.ctx.indent_level = 4 + extra
        # tuple[T | None, ...] has two distinct C++ shapes (storage vs borrow);
        # the inner iterator's __next__() returns borrow form for generators and
        # storage form for NativeIterables. `auto&&` binds to either without the
        # codegen having to know which.
        iter_elem_unwrapped = unwrap_ref_type(iter_elem) if iter_elem else None
        is_pointer_repr_tuple = (isinstance(iter_elem_unwrapped, TupleType)
                                 and iter_elem_unwrapped.has_pointer_repr_optional_element())
        if iter_elem and iter_elem.is_value_type() and not is_pointer_repr_tuple:
            out.write(f"{I(4)}{cpp_iter_elem} {cpp_var} = ::tpy::unwrap_ref(*__r);\n")
        else:
            out.write(f"{I(4)}auto&& {cpp_var} = ::tpy::unwrap_ref(*__r);\n")

        for stmt in pre_yield:
            self.statements.gen_stmt(out, stmt)
        yield_expr = self.statements.gen_yield_value(yield_stmt)
        out.write(f"{I(4)}{val_binding} __val = {yield_expr};\n")
        for stmt in post_yield:
            self.statements.gen_stmt(out, stmt)
        out.write(f"{I(4)}return std::optional<{cpp_iter_slot}>(__val);\n")
        out.write(f"{I(3)}}}\n")
        out.write(f"{I(2)}}}\n")
        out.write(f"{I(1)});\n")
        out.write(f"{I(0)}}}\n")

    @staticmethod
    def _iter_slot_for_yield(elem_type: 'TpyType', cpp_elem: str) -> str:
        """Pick the make_generator iterator slot type for a simple-generator yield.

        Iterator yields hand out references like function returns (CPython
        semantics), so borrow form is the default for tuple yields:
        `tuple[Int32, Point]` -> `std::tuple<int32_t, Point&>`,
        `tuple[P | None, P | None]` -> `std::tuple<P*, P*>`. The outer tuple
        is a value type that std::optional can hold, with reference-bearing
        inner elements preserved through the yield boundary.

        A bare non-value yield under `Iterator[T]` (BORROW_REF) wraps the slot
        in `val_or_ref<T>` -- a pointer-holding value wrapper -- so the
        std::optional / std::expected slot can hand out a reference to the live
        object instead of a copy (`std::optional<T&>` is ill-formed pre-C++26).
        `Iterator[Own[T]]` (OWNED) keeps the bare value slot (moved out), and a
        value-type element (VALUE) keeps the bare value (copies are free).

        A `readonly[tuple[...]]` yield must peel the ReadonlyType wrapper
        before the tuple check (`unwrap_ref_type` only strips RefType), else
        the slot collapses to value form and copies the elements;
        `to_cpp_return()` on the readonly tuple still yields the const-borrow
        form (`std::tuple<const T&, ...>`).
        """
        unwrapped = unwrap_ref_type(unwrap_readonly(elem_type))
        if isinstance(unwrapped, TupleType):
            return elem_type.to_cpp_return()
        # A bare reference element is handed out by reference via val_or_ref<T>.
        # yield_uses_borrow_slot excludes the forms with their own representation
        # (Optional/Union pointer-or-storage, readonly const-borrow, Own move,
        # TypeParamRef already val_or_ref-substituted by the caller).
        if yield_uses_borrow_slot(elem_type):
            return f"::tpy::val_or_ref<{cpp_elem}>"
        return cpp_elem

    @staticmethod
    def _yield_binds_by_ref(elem_type: 'TpyType', post_yield: list[TpyStmt]) -> bool:
        """Whether a simple-generator yield binds its value as `auto&&` (borrow).

        A concrete borrow yield must bind by reference unconditionally: its slot
        is `val_or_ref<T>` (stores a pointer), so a copied local would dangle. A
        generic (TypeParamRef) yield keeps the prior post_yield-guarded binding --
        its slot is the already-substituted `val_or_ref<ConcreteT>` / value, a
        cheap wrapper safe to copy, so auto&& is only used when no post-yield code
        could mutate the referenced source.
        """
        unwrapped = unwrap_ref_type(unwrap_readonly(elem_type))
        if isinstance(unwrapped, TypeParamRef):
            return not post_yield
        return yield_uses_borrow_slot(elem_type)

    @staticmethod
    def _split_at_yield(body: list[TpyStmt]) -> tuple[TpyYield, list[TpyStmt], list[TpyStmt]]:
        """Split a loop body at the yield statement. Returns (yield, pre, post)."""
        for i, stmt in enumerate(body):
            if isinstance(stmt, TpyYield):
                return stmt, body[:i], body[i + 1:]
        raise AssertionError("no yield found in body")

    def _analyze_for_strategy(self, stmt: TpyForEach, uid: int,
                              proto_param_names: frozenset[str] = frozenset()
                              ) -> GeneratorForInfo | None:
        """Determine the iteration strategy and struct fields for a for-loop with yield.

        `proto_param_names` is the set of static-protocol param names of the
        enclosing coro; iterating directly over one types the iterator frame
        field against its deduced template arg `T_<pname>` (see the universal
        strategy below) rather than the un-instantiable concept rendering.
        """
        from tpyc.modules import get_error_return_next_element_type

        elem_type = stmt.elem_type
        if elem_type and isinstance(elem_type, IntLiteralType):
            elem_type = self.ctx.analyzer.ctx.default_int_type
        elem_cpp = self.types.type_to_cpp(elem_type) if elem_type else "int32_t"

        # Range counter optimization
        if isinstance(stmt.iterable, TpyCall) and stmt.iterable.func_name == "range":
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

        # Built-in NativeIterable containers (list, dict, set, Array, Span, str) -- begin/end
        record = self.ctx.analyzer.registry.get_record_for_type(iterable_type)
        is_builtin_ni = (record is not None and record.is_native
                         and builtin_modules.is_native_iterable(iterable_type, registry=self.ctx.analyzer.registry))
        native_elem = builtin_modules.get_iterable_element_type(iterable_type, registry=self.ctx.analyzer.registry) if is_builtin_ni else None
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
            # Non-value element type with stable lvalue source: the loop var
            # becomes T* (aliasing the container element) instead of
            # std::optional<T> (value-copy). Preserves CPython aliasing
            # semantics across yield/resume.
            # ReadonlyType elements need a `const T*` slot (not plain `T*`);
            # the current emission path only emits the latter, so readonly
            # elements fall back to value-storage to avoid a
            # const-correctness violation when taking `&(*iter)`.
            elem_for_form = unwrap_ref_type(native_elem) if native_elem else None
            pointer_form_targets: frozenset[str] = frozenset()
            if (stmt.is_tuple_unpack and isinstance(elem_for_form, TupleType)
                    and stmt.body and isinstance(stmt.body[0], TpyTupleUnpack)):
                # Tuple-unpack over a stable lvalue container: alias the
                # non-value, non-readonly members so mutating an unpacked
                # record propagates to the source element (CPython semantics,
                # matching the plain pointer-form loop var). `__for_tup`
                # becomes T* and the aliased targets become T* too; value /
                # readonly members stay value-copy.
                unpack = stmt.body[0]
                # Optional members are excluded: they go through the existing
                # pointer-repr-Optional path (`T* = nullptr`, optional_to_ptr)
                # in the struct emit and `_gen_tuple_unpack`. Only plain
                # reference members alias via `&std::get<i>(...)`.
                aliased = {
                    tname
                    for tname, etype in zip(
                        unpack.targets, elem_for_form.element_types)
                    if tname is not None
                    and not unwrap_ref_type(etype).is_value_type()
                    and not isinstance(
                        unwrap_ref_type(etype), (ReadonlyType, OptionalType))
                }
                pointer_form_var = stmt.var if aliased else None
                pointer_form_targets = frozenset(aliased)
            else:
                pointer_form_var = (
                    stmt.var
                    if (elem_for_form is not None
                        and not elem_for_form.is_value_type()
                        and not isinstance(elem_for_form, ReadonlyType))
                    else None
                )
            return GeneratorForInfo(
                uid=uid, strategy="begin_end", fields=fields,
                pointer_form_loop_var=pointer_form_var,
                pointer_form_unpack_targets=pointer_form_targets,
            )

        # Universal default: ::tpy::__iter__() + __next__() loop.
        # Handles Iterable[T]/NativeIterable[T] protocol params, user types
        # with __iter__(), and any remaining iterable types.
        #
        # A direct loop over a static-protocol param sources from its deduced
        # template arg `T_<pname>` (the param's frame-field type), not
        # `type_to_cpp`, which renders the protocol as a C++ concept --
        # un-instantiable inside `std::declval`. The dual guard (name is a
        # classified param AND the resolved type is still a static protocol)
        # means a concrete-typed local that shadows the param name falls back
        # to the ordinary rendering.
        if (isinstance(stmt.iterable, TpyName)
                and stmt.iterable.name in proto_param_names
                and self.functions.protocols.is_static_protocol_param(iterable_type)):
            src_cpp = protocol_param_template_name(stmt.iterable.name)
        else:
            src_cpp = self.types.type_to_cpp(iterable_type)
        iter_field_type = f"std::decay_t<decltype(::tpy::__iter__(std::declval<{src_cpp}&>()))>"
        result_field_type = f"decltype(std::declval<{iter_field_type}&>().__next__())"
        fields = [
            (f"__for_itr_{uid}", iter_field_type),
            (f"__for_r_{uid}", result_field_type),
        ]
        return GeneratorForInfo(uid=uid, strategy="iter_next", fields=fields)

    def _is_named_generator_field(self, expr: TpyExpr) -> bool:
        """Check if expression is a named variable that's stable across yields.

        Returns True for named variables (params, locals, globals) since they
        persist across __next__() calls. Returns False for expressions (calls,
        constructors) that would need to be stored in a synthetic field.
        """
        return isinstance(expr, TpyName)

    def _setup_body_scope(self, out: TextIO, func: TpyFunction, init_stmts: list[TpyStmt],
                          indent_level: int = 1) -> None:
        """Generate init stmts and set up codegen scope."""
        from ..namespace import Namespace
        local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
        for pname, ptype in func.params:
            local_ns.bind_variable(pname, ptype)
        self.statements.gen_body(
            out, init_stmts, func.params, func.generator_yield_type,
            func, local_ns, indent_level=indent_level, is_method=False,
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

