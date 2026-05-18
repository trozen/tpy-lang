"""Code generation for `async def` functions (state-machine struct conforming
to Awaitable[T]).

Mirrors gen_generators.py but with the resumable-frame shape from
docs/ASYNC_DESIGN.md: each async def lowers to a struct with a
`poll(Waker) -> Poll<T>` method instead of `__next__() -> std::expected`.

Implementation status:
- Commit 1: no-await async defs.
- Commit 3 (this): single/multi-await, statically-resolved sub-coroutines.
  Awaits are restricted to top-level statement positions:
    * `x = await sub()` (assign / vardecl)
    * `await sub()` (statement-level expression)
    * `return await sub()` (return value)
- Subsequent commits: try/finally re-establishment, cancellation, await
  inside loops/conditionals.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, fields, is_dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from ..parse.nodes import (
    TpyFunction, TpyAwait, TpyStmt, TpyAssign, TpyVarDecl, TpyReturn,
    TpyExprStmt, TpyName, TpyExpr, TpyTry, TpyExceptHandler, TpyCall,
    TpyFieldAccess,
)
from ..typesys import unwrap_ref_type, unwrap_own, VoidType
from ..type_def_registry import is_str_type
from .context import INDENT, escape_cpp_name, CodeGenError, FinallyContext
from . import resumable_cfg as rcfg


if TYPE_CHECKING:
    from io import TextIO
    from .context import CodeGenContext
    from .types import TypeMapper
    from .expressions import ExpressionGenerator
    from .statements import StatementGenerator
    from .functions import FunctionGenerator


class _StateKind(IntEnum):
    INITIAL = 0
    RESUME = 1
    JOIN = 2


@dataclass(frozen=True, order=True)
class _StateLabel:
    """Typed switch-case label. Orders naturally (INITIAL < RESUME < JOIN
    by `IntEnum` value, then by `idx`); formats to its C++ identifier
    via `cpp_name()`."""
    kind: _StateKind
    idx: int = 0

    def cpp_name(self) -> str:
        if self.kind is _StateKind.INITIAL:
            return "S_INITIAL"
        if self.kind is _StateKind.RESUME:
            return f"S_AFTER_AWAIT_{self.idx}"
        return f"S_JOIN_{self.idx}"


# Single C++ spelling of `Ready(unit)` for void-returning async defs.
# `async def -> None` lowers Poll[None]'s type-arg slot to std::monostate
# (the value-bearing-None unit type); the `ready` factory needs an actual
# value of that type, so we construct `std::monostate{}` explicitly.
# Centralised here so the 3 emit sites in this module + 1 in statements.py
# can't drift apart.
POLL_VOID_READY_RETURN = (
    "return ::tpystd::tpy::Poll<::std::monostate>::ready(::std::monostate{});"
)


class AsyncCoroCodegen:
    """Generates C++ code for `async def` functions as state-machine structs."""

    def __init__(
        self,
        ctx: "CodeGenContext",
        types: "TypeMapper",
        expressions: "ExpressionGenerator",
        statements: "StatementGenerator",
        functions: "FunctionGenerator",
    ):
        self.ctx = ctx
        self.types = types
        self.expressions = expressions
        self.statements = statements
        self.functions = functions

    @staticmethod
    def gen_struct_name(func: TpyFunction) -> str:
        """Coro struct name: `__coro_<funcname>` (mirrors `__gen_<funcname>`
        for generator structs)."""
        return f"__coro_{escape_cpp_name(func.name)}"

    @staticmethod
    def _sub_struct_name(awaited_func_name: str) -> str:
        """Sub-coroutine struct name for a statically-resolved await
        (must match `gen_struct_name` for the awaited async def)."""
        return f"__coro_{escape_cpp_name(awaited_func_name)}"

    def _classify_params(self, func: TpyFunction) -> list[tuple[str, str, bool]]:
        """Classify async-def params for the coro struct field/ctor shape.

        Returns list of (cpp_name, cpp_type, is_ref).

        v1 conservative rule: str passes by string_view (caller's storage,
        same lifetime model as generators -- coros that escape via Task
        will need to copy out, but until that lands the borrow holds
        across await boundaries within a single asyncio.run).
        """
        out: list[tuple[str, str, bool]] = []
        for pname, ptype in func.params:
            cpp_name = escape_cpp_name(pname)
            ptype_inner = unwrap_ref_type(ptype)
            if is_str_type(ptype_inner):
                out.append((cpp_name, "std::string_view", False))
            else:
                cpp_type = self.types.type_to_cpp(ptype_inner)
                is_ref = not ptype_inner.is_value_type()
                out.append((cpp_name, cpp_type, is_ref))
        return out

    def _emit_template_header(self, out: "TextIO", func: TpyFunction) -> bool:
        if not func.type_params:
            return False
        params = ", ".join(f"typename {tp}" for tp in func.type_params)
        out.write(f"template <{params}>\n")
        return True

    def _emit_params_decl(self, func: TpyFunction) -> str:
        parts: list[str] = []
        for cpp_name, cpp_type, is_ref in self._classify_params(func):
            if is_ref:
                parts.append(f"{cpp_type}& {cpp_name}")
            else:
                parts.append(f"{cpp_type} {cpp_name}")
        return ", ".join(parts)

    def _ret_cpp(self, func: TpyFunction) -> str:
        return self.types.type_to_cpp(unwrap_ref_type(func.return_type))

    def _is_void_return(self, func: TpyFunction) -> bool:
        """True iff func's declared return is `None` -- i.e. the top-level
        `-> None` annotation that lowers to C++ `void`. Distinguished from
        `Task[None]` / `Poll[None]` consumers, where the inner None is at
        a type-arg position and routes to `std::monostate`."""
        return isinstance(unwrap_ref_type(func.return_type), VoidType)

    def _poll_ret_cpp(self, func: TpyFunction) -> str:
        # The state-machine's __poll__ returns Poll[T], a type-arg slot,
        # so a `-> None` async def flips its inner T to the unit type --
        # matching how Task[None] / Future[None] / Awaitable[None] lower
        # elsewhere. Without the flip, `Task<std::monostate>::poll_frame()`
        # would call a `Poll<void> __poll__()` and the C++ compiler would
        # reject the return-type mismatch.
        if self._is_void_return(func):
            return "::tpystd::tpy::Poll<::std::monostate>"
        return f"::tpystd::tpy::Poll<{self._ret_cpp(func)}>"

    # -- Forward declarations -------------------------------------------------

    def gen_coro_forward_decl(self, out: "TextIO", func: TpyFunction) -> bool:
        struct_name = self.gen_struct_name(func)
        self._emit_template_header(out, func)
        out.write(f"struct {struct_name};\n")
        return True

    def gen_factory_forward_decl(self, out: "TextIO", func: TpyFunction) -> bool:
        struct_name = self.gen_struct_name(func)
        self._emit_template_header(out, func)
        params = self._emit_params_decl(func)
        out.write(f"{struct_name} {escape_cpp_name(func.name)}({params});\n")
        return True

    # -- Body partitioning ----------------------------------------------------

    def _effective_body(self, func: TpyFunction) -> list[TpyStmt]:
        """Return the function body with the await-lift pass applied so
        awaits embedded in expressions become top-level vardecls. The
        CFG builder treats compound statements (including any
        wrapping try/finally) uniformly, so no unwrap is needed here.

        Cached on the TpyFunction node so multiple codegen passes
        (struct emission + poll body) see the same rewrite.
        """
        cached = getattr(func, "_async_lifted_body", None)
        if cached is not None:
            return cached
        lifted = self._lift_nested_awaits(func, func.body)
        func._async_lifted_body = lifted
        return lifted

    def _lift_nested_awaits(self, func: TpyFunction,
                             body: list[TpyStmt]) -> list[TpyStmt]:
        """Rewrite each statement that has awaits buried in expressions
        into a sequence of statements where every await is at top level.

        Recurses into compound statement bodies (if/while/for/try/with)
        so awaits buried inside loop/branch bodies are also lifted.

        Example:
            x = (await a()) + (await b())
        becomes:
            __await_lift_0 = await a()
            __await_lift_1 = await b()
            x = __await_lift_0 + __await_lift_1

        Each lifted name is registered as a hoisted local so the bind
        slot is a frame field.
        """
        out: list[TpyStmt] = []
        for stmt in body:
            top_await = rcfg._top_level_await_in(stmt)
            new_stmts = self._lift_awaits_in_stmt(func, stmt, top_await)
            # Recurse compound bodies AFTER the surface lift so a
            # substituted TpyName isn't re-scanned. The surface lift
            # mutates `stmt` in place (see BUGS.md "Await lifter
            # mutates parse AST"); the compound recurse returns a
            # shallow copy so nested bodies don't.
            new_stmts = [
                self._lift_compound_subbodies(func, s) for s in new_stmts
            ]
            out.extend(new_stmts)
        return out

    def _lift_compound_subbodies(self, func: TpyFunction,
                                    stmt: TpyStmt) -> TpyStmt:
        """If `stmt` is a compound (if/while/for/try/with) and any of
        its sub-bodies needs lifting, return a shallow copy with the
        sub-body lists replaced by lifted versions. Otherwise return
        `stmt` unchanged. Does not mutate the input."""
        if not hasattr(stmt, "sub_bodies"):
            return stmt
        if not is_dataclass(stmt):
            return stmt
        # Compute per-field lifted sub-bodies. Track whether any field
        # actually changed; if none did, return the original stmt.
        replacements: dict[str, list[TpyStmt]] = {}
        for f in fields(stmt):
            v = getattr(stmt, f.name, None)
            if isinstance(v, list) and v and isinstance(v[0], TpyStmt):
                lifted = self._lift_nested_awaits(func, v)
                # `lifted` is always a NEW list returned by
                # _lift_nested_awaits, so use it directly (cheap to
                # reassign even if contents are the same references).
                replacements[f.name] = lifted
        # TpyTry: handlers list isn't TpyStmt-typed but each handler
        # carries its own body. Build new handler instances if any
        # handler body needed lifting.
        new_handlers: list[TpyExceptHandler] | None = None
        if isinstance(stmt, TpyTry) and stmt.handlers:
            rebuilt: list[TpyExceptHandler] = []
            any_changed = False
            for h in stmt.handlers:
                lifted_body = self._lift_nested_awaits(func, h.body) if h.body else h.body
                if lifted_body is not h.body and lifted_body != h.body:
                    rebuilt.append(TpyExceptHandler(
                        exception_type=h.exception_type,
                        binding=h.binding,
                        body=lifted_body,
                        loc=h.loc,
                    ))
                    any_changed = True
                else:
                    rebuilt.append(h)
            if any_changed:
                new_handlers = rebuilt
        if not replacements and new_handlers is None:
            return stmt
        new_stmt = copy.copy(stmt)
        for fname, lifted in replacements.items():
            setattr(new_stmt, fname, lifted)
        if new_handlers is not None:
            new_stmt.handlers = new_handlers
        return new_stmt

    def _lift_awaits_in_stmt(self, func: TpyFunction, stmt: TpyStmt,
                              top_await: 'TpyAwait | None') -> list[TpyStmt]:
        """Lift any TpyAwait inside `stmt`'s expressions out into preceding
        VarDecls. The top-level await (if any) stays in place. Returns the
        new statement list; if no rewrite needed, returns [stmt].
        """
        # Collect nested awaits (excluding the top-level one).
        lifts: list[TpyAwait] = []
        self._collect_nested_awaits(stmt, top_await, lifts)
        if not lifts:
            return [stmt]
        new_stmts: list[TpyStmt] = []
        for await_node in lifts:
            name = f"__await_lift_{self._next_lift_id(func)}"
            self._bump_lift_id(func)
            await_t = self.ctx.get_expr_type(await_node)
            if await_t is None:
                raise CodeGenError(
                    "await result has no analyzed type (lift)", loc=stmt.loc)
            await_t = unwrap_ref_type(await_t)
            new_decl = TpyVarDecl(
                name=name, type=await_t, init=await_node, loc=stmt.loc)
            new_stmts.append(new_decl)
            # Register as hoisted local; codegen emits a frame field.
            # Append in place to avoid an O(N) copy per lift (which,
            # paired with _next_lift_id's linear scan, would otherwise
            # be O(N^2) over nested awaits in one function).
            if func.generator_locals is None:
                func.generator_locals = []
            func.generator_locals.append((name, await_t))
            # Replace the await in `stmt`'s expression tree with a
            # TpyName referring to the lifted local. Use the analyzer's
            # set_expr_type so codegen's get_expr_type sees it.
            replacement = TpyName(name=name, loc=await_node.loc)
            self.ctx.analyzer.ctx.set_expr_type(replacement, await_t)
            self._replace_expr_in_stmt(stmt, await_node, replacement)
        new_stmts.append(stmt)
        return new_stmts

    def _collect_nested_awaits(self, stmt: TpyStmt, top_await: 'TpyAwait | None',
                                out: list[TpyAwait]) -> None:
        """Walk stmt's expressions; collect TpyAwait nodes that are NOT the
        top-level await (which is handled by region partitioning). Skips
        nested defs / sub_bodies."""

        def walk_expr(e):
            if e is None:
                return
            if isinstance(e, TpyAwait):
                if e is not top_await:
                    out.append(e)
                # Don't recurse into the await's own value; that becomes
                # the call processed by the lifted VarDecl.
                return
            for c in (e.children() if hasattr(e, "children") else ()):
                walk_expr(c)

        if hasattr(stmt, "exprs"):
            for e in stmt.exprs():
                walk_expr(e)

    def _next_lift_id(self, func: TpyFunction) -> int:
        """Return the next free index for __await_lift_<n> names.
        Tracked as a per-function counter so the lift pass is linear
        in the total number of lifts."""
        cur = getattr(func, "_async_next_lift_id", 0)
        return cur

    def _bump_lift_id(self, func: TpyFunction) -> None:
        func._async_next_lift_id = getattr(func, "_async_next_lift_id", 0) + 1

    def _replace_expr_in_stmt(self, stmt: TpyStmt, old_expr,
                                new_expr) -> None:
        """Walk stmt's dataclass fields and replace `old_expr` with
        `new_expr` (by identity). Catches both primary-expression slots
        (init/value/expr/condition for if/while/etc.) and nested
        expression children."""
        if not is_dataclass(stmt):
            return
        for f in fields(stmt):
            v = getattr(stmt, f.name, None)
            if v is old_expr:
                setattr(stmt, f.name, new_expr)
                return
            if isinstance(v, list):
                for i, item in enumerate(v):
                    if item is old_expr:
                        v[i] = new_expr
                        return
                    if hasattr(item, "children"):
                        self._replace_in_expr(item, old_expr, new_expr)
            elif hasattr(v, "children"):
                self._replace_in_expr(v, old_expr, new_expr)

    def _replace_in_expr(self, expr, old_expr, new_expr) -> None:
        """Recursively replace `old_expr` with `new_expr` inside `expr`."""
        if not hasattr(expr, "children") or not is_dataclass(expr):
            return
        for f in fields(expr):
            v = getattr(expr, f.name, None)
            if v is old_expr:
                setattr(expr, f.name, new_expr)
                return
            if isinstance(v, list):
                for i, item in enumerate(v):
                    if item is old_expr:
                        v[i] = new_expr
                        return
                    if hasattr(item, "children"):
                        self._replace_in_expr(item, old_expr, new_expr)
            elif hasattr(v, "children"):
                self._replace_in_expr(v, old_expr, new_expr)

    # -- Struct definition ----------------------------------------------------

    def gen_coro_struct(self, out: "TextIO", func: TpyFunction) -> None:
        """Emit the full `__FCoro` struct definition."""
        struct_name = self.gen_struct_name(func)
        ctor_params = self._classify_params(func)
        cfg = self._build_cfg(func)
        yields = cfg.yield_sites

        out.write(f"// Async coroutine: {func.name}\n")
        self._emit_template_header(out, func)
        out.write(f"struct {struct_name} {{\n")

        # State + cancel flag
        out.write(f"{INDENT}int32_t __state;\n")
        out.write(f"{INDENT}bool __cancel_pending;\n")

        # Captured param fields
        for cpp_name, cpp_type, is_ref in ctor_params:
            if is_ref:
                out.write(f"{INDENT}{cpp_type}& {cpp_name};\n")
            else:
                out.write(f"{INDENT}{cpp_type} {cpp_name};\n")

        # Hoisted local fields (mirrors generator behavior).
        if func.generator_locals:
            for lname, ltype in func.generator_locals:
                ltype_inner = unwrap_ref_type(ltype)
                cpp_type = self.types.type_to_cpp(ltype_inner)
                cpp_name = escape_cpp_name(lname)
                if ltype_inner.is_value_type():
                    out.write(f"{INDENT}{cpp_type} {cpp_name};\n")
                else:
                    out.write(f"{INDENT}std::optional<{cpp_type}> {cpp_name};\n")

        # Sub-future fields: one per Yield (suspension_index = field
        # ordinal). Inline mode: optional<__<name>Coro>; Erased: optional
        # of the value awaitable; Borrowed: raw pointer.
        for y in yields:
            p = y.payload
            if p.mode is rcfg.AwaitMode.BORROWED:
                out.write(f"{INDENT}{p.sub_field_cpp_type}* "
                          f"__sub_{y.suspension_index} = nullptr;\n")
            else:
                out.write(f"{INDENT}std::optional<{p.sub_field_cpp_type}> "
                          f"__sub_{y.suspension_index};\n")

        out.write(f"\n")

        # State enum: S_INITIAL, S_AFTER_AWAIT_<i>, S_JOIN_<n>, S_DONE.
        # State numbering matches _compute_case_entries' assignment.
        case_entries = self._compute_case_entries(cfg)
        ordered = sorted(case_entries.items(), key=lambda kv: kv[1])
        out.write(f"{INDENT}enum : int32_t {{\n")
        next_val = 0
        for _bb_id, label in ordered:
            out.write(f"{INDENT}{INDENT}{label.cpp_name()} = {next_val},\n")
            next_val += 1
        out.write(f"{INDENT}{INDENT}S_DONE = {next_val},\n")
        out.write(f"{INDENT}}};\n\n")

        # Constructor.
        ctor_param_list = ", ".join(
            f"{cpp_type}& {cpp_name}" if is_ref else f"{cpp_type} {cpp_name}_"
            for cpp_name, cpp_type, is_ref in ctor_params
        )
        init_parts = ["__state(S_INITIAL)", "__cancel_pending(false)"]
        for cpp_name, _, is_ref in ctor_params:
            if is_ref:
                init_parts.append(f"{cpp_name}({cpp_name})")
            else:
                init_parts.append(f"{cpp_name}(std::move({cpp_name}_))")
        out.write(f"{INDENT}{struct_name}({ctor_param_list})\n")
        out.write(f"{INDENT}{INDENT}: {', '.join(init_parts)} {{}}\n\n")

        # __poll__() forward declaration.
        out.write(f"{INDENT}{self._poll_ret_cpp(func)} __poll__(::tpy::Waker waker);\n")
        out.write(f"{INDENT}void cancel() {{ __cancel_pending = true; }}\n")

        # Finally-helper forward declarations: one per TryRegion with a
        # finally body.
        for helper_name, _body in cfg.finally_helpers:
            out.write(f"{INDENT}void {helper_name}();\n")

        out.write(f"\n{INDENT}friend std::ostream& operator<<("
                  f"std::ostream& os, const {struct_name}&) {{\n")
        out.write(f"{INDENT}{INDENT}return os << \"<coroutine {func.name}>\";\n")
        out.write(f"{INDENT}}}\n")
        out.write(f"}};\n")

    # -- Factory function -----------------------------------------------------

    def gen_factory(self, out: "TextIO", func: TpyFunction) -> None:
        """Emit the factory function: `__FCoro f(args) { return __FCoro(args); }`."""
        struct_name = self.gen_struct_name(func)
        self.ctx.emit_source_comment(out, func.loc)
        self._emit_template_header(out, func)
        params = self._emit_params_decl(func)
        out.write(f"{struct_name} {escape_cpp_name(func.name)}({params}) {{\n")
        if func.params:
            args = ", ".join(escape_cpp_name(pname) for pname, _ in func.params)
            out.write(f"{INDENT}return {struct_name}({args});\n")
        else:
            out.write(f"{INDENT}return {struct_name}();\n")
        out.write(f"}}\n")

    # -- poll() body ----------------------------------------------------------

    def gen_coro_finally_top_def(self, out: "TextIO", func: TpyFunction) -> None:
        """Emit member-function bodies for every `__finally_<n>()` helper
        the CFG produced (one per TryRegion with a finally body). No-op
        if the function has no finally bodies.
        """
        cfg = self._build_cfg(func)
        if not cfg.finally_helpers:
            return
        struct_name = self.gen_struct_name(func)

        # Set up field-rewrite ctx once for all helpers (they share the
        # same frame layout).
        old_in_gen = self.ctx.in_generator_body
        old_field_names = self.ctx.generator_field_names
        old_optional_fields = self.ctx.generator_optional_fields
        old_self_ref = self.ctx.generator_self_ref
        old_for_info = self.ctx.generator_for_loop_info
        self.ctx.in_generator_body = True
        self.ctx.generator_field_names = set()
        self.ctx.generator_optional_fields = set()
        self.ctx.generator_for_loop_info = {}
        self.ctx.generator_self_ref = None
        for pname, _ in func.params:
            self.ctx.generator_field_names.add(pname)
        if func.generator_locals:
            for lname, ltype in func.generator_locals:
                self.ctx.generator_field_names.add(lname)
                if not unwrap_ref_type(ltype).is_value_type():
                    self.ctx.generator_optional_fields.add(lname)

        try:
            for helper_name, body_stmts in cfg.finally_helpers:
                self._emit_template_header(out, func)
                out.write(f"void {struct_name}::{helper_name}() {{\n")
                self.ctx.indent_level = 1
                for stmt in body_stmts:
                    self.statements.gen_stmt(out, stmt)
                self.ctx.indent_level = 0
                out.write(f"}}\n")
        finally:
            self.ctx.in_generator_body = old_in_gen
            self.ctx.generator_field_names = old_field_names
            self.ctx.generator_optional_fields = old_optional_fields
            self.ctx.generator_for_loop_info = old_for_info
            self.ctx.generator_self_ref = old_self_ref

    def gen_coro_poll_def(self, out: "TextIO", func: TpyFunction) -> None:
        """Emit the `poll()` method body in the .cpp file (or inline-in-hpp
        for templates -- the caller handles placement).
        """
        struct_name = self.gen_struct_name(func)
        cfg = self._build_cfg(func)
        has_yields = bool(cfg.yield_sites)

        self.ctx.emit_source_comment(out, func.loc)
        self._emit_template_header(out, func)
        out.write(f"{self._poll_ret_cpp(func)} {struct_name}::__poll__(::tpy::Waker waker) {{\n")
        if not has_yields:
            # No awaits: waker unused. Generators emit (void)waker for the
            # same reason; reuse the pattern.
            out.write(f"{INDENT}(void)waker;\n")

        # Set up async-coro context (reuses generator field-rewrite path
        # plus async-specific return rewrite).
        old_in_gen = self.ctx.in_generator_body
        old_field_names = self.ctx.generator_field_names
        old_optional_fields = self.ctx.generator_optional_fields
        old_for_info = self.ctx.generator_for_loop_info
        old_self_ref = self.ctx.generator_self_ref
        old_in_async = getattr(self.ctx, "in_async_coro_body", False)
        old_async_ret_cpp = getattr(self.ctx, "async_coro_return_cpp", None)
        old_async_done_label = getattr(self.ctx, "async_coro_done_state", None)

        self.ctx.in_generator_body = True
        self.ctx.generator_field_names = set()
        self.ctx.generator_optional_fields = set()
        self.ctx.generator_for_loop_info = {}
        self.ctx.generator_self_ref = None

        self.ctx.in_async_coro_body = True
        self.ctx.async_coro_return_cpp = self._ret_cpp(func)
        self.ctx.async_coro_done_state = "S_DONE"

        # Populate field-rewrite sets: params + hoisted locals are frame fields.
        for pname, _ in func.params:
            self.ctx.generator_field_names.add(pname)
        if func.generator_locals:
            for lname, ltype in func.generator_locals:
                self.ctx.generator_field_names.add(lname)
                if not unwrap_ref_type(ltype).is_value_type():
                    self.ctx.generator_optional_fields.add(lname)

        # Save current_return_type so statements.py's TpyReturn handler
        # sees the right context.
        self.ctx.current_return_type = func.return_type

        try:
            # Emit while(true) switch + each case body via the CFG.
            self._emit_state_machine(out, func, cfg)
        finally:
            self.ctx.in_generator_body = old_in_gen
            self.ctx.generator_field_names = old_field_names
            self.ctx.generator_optional_fields = old_optional_fields
            self.ctx.generator_for_loop_info = old_for_info
            self.ctx.generator_self_ref = old_self_ref
            self.ctx.in_async_coro_body = old_in_async
            self.ctx.async_coro_return_cpp = old_async_ret_cpp
            self.ctx.async_coro_done_state = old_async_done_label

        out.write(f"}}\n")

    # =====================================================================
    # CFG-based state-machine emitter (replaces _emit_switch_body).
    # =====================================================================

    def _build_cfg(self, func: TpyFunction) -> 'rcfg.CFG':
        """Apply the await-lift pre-pass, then build the CFG. The CFG
        builder handles any wrapping try/finally uniformly with all
        other compound statements -- no special unwrap-and-rewrap pass
        is needed."""
        cached = getattr(func, "_async_cfg", None)
        if cached is not None:
            return cached
        body = self._effective_body(func)
        builder = rcfg.CFGBuilder(payload_factory=self._make_await_payload)
        try:
            cfg = builder.build_async(body)
        except rcfg._CFGNotYetSupported as e:
            raise CodeGenError(e.msg, loc=e.loc)
        # Stash the builder so callers (emit) can look up handler
        # entries via builder.get_handler_entry().
        func._async_cfg_builder = builder
        func._async_cfg = cfg
        return cfg

    def _make_await_payload(self, await_node: TpyAwait, host_stmt: TpyStmt,
                             kind, bind_target, return_stmt) -> 'rcfg.AwaitPayload':
        """CFGBuilder payload factory: derives mode + sub_field_cpp_type
        from the await's sema annotations."""
        if await_node.awaited_async_func_name is not None:
            mode = rcfg.AwaitMode.INLINE
            sub_cpp = self._sub_struct_name(await_node.awaited_async_func_name)
        elif await_node.awaited_task_inner is not None:
            operand_type = self.ctx.get_expr_type(await_node.value)
            if operand_type is None:
                raise CodeGenError(
                    "await operand has no analyzed type",
                    loc=host_stmt.loc)
            operand_inner = unwrap_own(unwrap_ref_type(operand_type))
            sub_cpp = self.types.type_to_cpp(operand_inner)
            if operand_inner.is_value_type():
                mode = rcfg.AwaitMode.ERASED
            elif self._is_stable_lvalue(await_node.value):
                mode = rcfg.AwaitMode.BORROWED
            else:
                mode = rcfg.AwaitMode.ERASED
        else:
            raise CodeGenError(
                "await reached codegen without sema-resolved sub-future shape",
                loc=host_stmt.loc)
        return rcfg.AwaitPayload(
            mode=mode,
            sub_field_cpp_type=sub_cpp,
            operand_expr=await_node.value,
            kind=kind,
            bind_target=bind_target,
            return_stmt=return_stmt,
            host_stmt=host_stmt,
            await_node=await_node,
        )

    def _compute_case_entries(self, cfg: 'rcfg.CFG') -> dict[int, _StateLabel]:
        """Return mapping bb_id -> StateLabel for every BB that needs its
        own case label in the emitted switch. Cached on the CFG so the
        struct-emit and poll-emit passes share the result.

        Rules: a BB is a case entry iff it is
        - the entry BB (S_INITIAL),
        - the resume_bb of a Yield (S_AFTER_AWAIT_<i>),
        - reached via Fall/Branch by 2+ predecessors (multi-pred join), or
        - reached via Fall/Branch from a predecessor with a different
          region_stack (region-crossing edge).

        Handler entries (reached only via C++ catch) are inlined into
        their enclosing try-region's catch and never get a case label.
        """
        if cfg._case_entries_cache is not None:
            return cfg._case_entries_cache
        case_entries: dict[int, _StateLabel] = {
            cfg.entry_bb: _StateLabel(_StateKind.INITIAL)
        }
        for y in cfg.yield_sites:
            case_entries[y.resume_bb] = _StateLabel(
                _StateKind.RESUME, y.suspension_index)
        # Compute predecessors via Fall/Branch/Yield-resume edges.
        # Yield.resume_bb already a case entry; tracking helps detect
        # multi-pred / region-crossing.
        preds: dict[int, list[int]] = {bid: [] for bid in cfg.blocks}
        for bid, bb in cfg.blocks.items():
            t = bb.terminator
            if isinstance(t, rcfg.Fall):
                preds[t.next_bb].append(bid)
            elif isinstance(t, rcfg.Branch):
                preds[t.then_bb].append(bid)
                preds[t.else_bb].append(bid)
            elif isinstance(t, rcfg.Yield):
                preds[t.resume_bb].append(bid)
        # Multi-pred (excluding yield-resume, which is already case entry).
        join_idx = 0
        for bid, ps in preds.items():
            if bid in case_entries:
                continue
            if len(ps) >= 2:
                case_entries[bid] = _StateLabel(_StateKind.JOIN, join_idx)
                join_idx += 1
        # Region-crossing predecessors.
        for bid, bb in cfg.blocks.items():
            if bid in case_entries:
                continue
            for p in preds.get(bid, ()):
                p_bb = cfg.blocks[p]
                if p_bb.region_stack != bb.region_stack:
                    case_entries[bid] = _StateLabel(_StateKind.JOIN, join_idx)
                    join_idx += 1
                    break
        cfg._case_entries_cache = case_entries
        return case_entries

    def _emit_state_machine(self, out: "TextIO", func: TpyFunction,
                             cfg: 'rcfg.CFG') -> None:
        """Emit `while (true) switch (state) { ... }` for the CFG.
        For zero-yield async defs the loop is omitted (no state
        transitions can fire, so the switch runs once)."""
        case_entries = self._compute_case_entries(cfg)
        self.ctx.indent_level = 1
        inner = self.ctx.indent()
        if cfg.yield_sites:
            out.write(f"{inner}while (true) switch (__state) {{\n")
        else:
            out.write(f"{inner}switch (__state) {{\n")
        # Case-label order matches the enum in gen_coro_struct.
        order = sorted(case_entries.items(), key=lambda kv: kv[1])
        for bb_id, label in order:
            out.write(f"{inner}case {label.cpp_name()}: {{\n")
            self.ctx.indent_level = 2
            self._emit_case(out, cfg, bb_id, case_entries, func)
            self.ctx.indent_level = 1
            out.write(f"{inner}}}\n")
        out.write(f"{inner}case S_DONE: ::tpy::tpy_panic(\"poll after Ready\");\n")
        out.write(f"{inner}}}\n")
        out.write(f"{inner}__builtin_unreachable();\n")
        self.ctx.indent_level = 0

    def _emit_case(self, out: "TextIO", cfg: 'rcfg.CFG',
                    entry_bb: int, case_entries: dict[int, _StateLabel],
                    func: TpyFunction) -> None:
        """Emit the body of one switch case: wrap in region_stack, then
        walk the BB graph inline until hitting another case entry or a
        terminator that exits poll()."""
        bb = cfg.blocks[entry_bb]
        body_indent = self.ctx.indent()
        # Push FinallyContext entries so a `return` inside this case
        # body walks the right finally chain via _emit_finally_chain.
        # ExceptRegion contributes its parent try's finally because
        # Python runs finally after the handler completes.
        pushed_finally = self._push_finally_helpers(
            self._finally_helpers_for_region_stack(bb.region_stack))
        try:
            # Emit `try {` for each TryRegion. Also collect, per
            # TryRegion, the list of "inter-region finally helpers" that
            # sit between THIS TryRegion and the next-inner TryRegion in
            # the case's region_stack (each ExceptRegion's parent_finally,
            # plus any FinallyRegion helper). These must run in the
            # TryRegion's catch-all before its own finally, because they
            # represent Python-level finallies whose source-level try
            # frame is no longer in the C++ try stack but is still
            # logically active until control unwinds past this
            # TryRegion.
            tryctx_stack: list[tuple[rcfg.Region, str, tuple[str, ...]]] = []
            try_indices = [i for i, r in enumerate(bb.region_stack)
                            if isinstance(r, rcfg.TryRegion)]
            for j, i in enumerate(try_indices):
                region = bb.region_stack[i]
                # Find the next TryRegion (or end of stack); inter-region
                # entries between i+1 and that index contribute extra
                # finallies to this TryRegion's catch-all (innermost
                # first, i.e. reversed source order).
                next_try = try_indices[j + 1] if j + 1 < len(try_indices) else len(bb.region_stack)
                extras: list[str] = []
                for k in range(i + 1, next_try):
                    mid = bb.region_stack[k]
                    if isinstance(mid, rcfg.ExceptRegion):
                        if mid.parent_finally is not None:
                            extras.append(mid.parent_finally)
                    # FinallyRegion / WithRegion: future work.
                extras.reverse()  # innermost-first on the unwind path
                out.write(f"{body_indent}try {{\n")
                tryctx_stack.append((region, body_indent, tuple(extras)))
                self.ctx.indent_level += 1
                body_indent = self.ctx.indent()

            # Emit the case body: resume step if this is a yield-resume,
            # then BB statements, then terminator.
            self._emit_case_body(out, cfg, entry_bb, case_entries, func)

            # Close regions innermost first.
            for region, outer_indent, extras in reversed(tryctx_stack):
                self.ctx.indent_level -= 1
                body_indent = self.ctx.indent()
                out.write(f"{body_indent}}}")
                # Emit handler catches for the inner try.
                self._emit_try_region_catches(
                    out, body_indent, region, cfg, case_entries,
                    entry_bb, func, extras)
                out.write("\n")
        finally:
            for _ in range(pushed_finally):
                self.ctx.finally_stack.pop()

    def _finally_helpers_for_region_stack(
            self, region_stack: tuple) -> list[str]:
        """Compute the finally helper names that should be on
        ctx.finally_stack while emitting BBs with this region_stack.
        Each TryRegion contributes its `finally_helper_name`; each
        ExceptRegion contributes its `parent_finally`. Order: outermost
        first (so the innermost ends up at the top of the stack)."""
        helpers: list[str] = []
        for region in region_stack:
            if isinstance(region, rcfg.TryRegion):
                if region.finally_helper_name is not None:
                    helpers.append(region.finally_helper_name)
            elif isinstance(region, rcfg.ExceptRegion):
                if region.parent_finally is not None:
                    helpers.append(region.parent_finally)
        return helpers

    def _push_finally_helpers(self, helpers: list[str]) -> int:
        """Push FinallyContext entries for each helper. Returns the
        count pushed for matching pop in a `finally:` clause."""
        count = 0
        for name in helpers:
            helper_name = name
            def _emit_finally(o: "TextIO", ind: str, n=helper_name) -> None:
                o.write(f"{ind}this->{n}();\n")
            fctx = FinallyContext(
                emit_finally=_emit_finally, terminates=False, loop_depth=0)
            self.ctx.finally_stack.append(fctx)
            count += 1
        return count

    def _emit_try_region_catches(self, out: "TextIO", indent: str,
                                  region: 'rcfg.TryRegion',
                                  cfg: 'rcfg.CFG',
                                  case_entries: dict[int, _StateLabel],
                                  case_entry_bb: int,
                                  func: TpyFunction,
                                  extra_finallies: tuple[str, ...] = ()) -> None:
        """Emit `} catch (...) { ... }` clauses for a TryRegion at the
        close of a case body. Each handler's body is emitted inline
        inside its catch (walks the handler entry BB).

        `case_entry_bb` is the case-entry BB this region wraps; used to
        determine which __sub_<n> to reset (the in-flight sub-future
        for this resume case).

        `extra_finallies` are helper-fn names (innermost first) for any
        ExceptRegion / FinallyRegion in the case's region_stack that
        sit *between* this TryRegion and the next-inner TryRegion --
        their Python-level try frames are no longer C++ try wraps here
        but are still logically active. They run in the catch-all (and
        each handler's body, if the handler completes normally is the
        Fall-edge case handled by `_emit_exit_region_finallies`; the
        throw escape is what we cover here)."""
        builder = getattr(func, "_async_cfg_builder", None)
        yield_for_case = self._yield_at_resume(cfg, case_entry_bb)
        # Helpers to invoke on throw from inside the handler body
        # (innermost first): each extra_finally + this region's own
        # finally. Mirrors the catch-all unwind order.
        handler_throw_finallies: tuple[str, ...] = tuple(extra_finallies)
        if region.finally_helper_name is not None:
            handler_throw_finallies = handler_throw_finallies + (region.finally_helper_name,)
        for handler in region.handlers:
            self.statements._emit_except_handler_header(out, handler)
            self.ctx.indent_level += 1
            catch_indent = self.ctx.indent()
            # Reset the in-flight sub-future first action in catch.
            if yield_for_case is not None:
                self._emit_sub_reset(out, catch_indent,
                                      yield_for_case.payload,
                                      yield_for_case.suspension_index)
            # Wrap handler body in `try { ... } catch (...) {
            # finallies; throw; }` so a `raise` from inside the
            # handler runs this try's finally (and any inter-region
            # finallies) before propagating to the outer try.
            has_throw_unwind = bool(handler_throw_finallies)
            if has_throw_unwind:
                out.write(f"{catch_indent}try {{\n")
                self.ctx.indent_level += 1
            # Swap ctx.finally_stack to match the handler entry BB's
            # region_stack. The case-entry's stack contains finallies
            # for regions that are no longer active inside the handler
            # body (the try whose handler we're in is gone). Without
            # the swap, a return inside the handler walks finallies
            # that should not apply (e.g. a sibling inner try's finally
            # that has already run on the throw path).
            old_finally_stack = self.ctx.finally_stack
            handler_entry: int | None = None
            if builder is not None:
                handler_entry = builder.get_handler_entry(region, handler)
            handler_stack_helpers: list[str] = []
            if handler_entry is not None:
                handler_bb = cfg.blocks[handler_entry]
                handler_stack_helpers = self._finally_helpers_for_region_stack(
                    handler_bb.region_stack)
            self.ctx.finally_stack = []
            self._push_finally_helpers(handler_stack_helpers)
            old_except_tier = self.ctx.in_except_tier
            self.ctx.in_except_tier = "throw"
            try:
                if handler_entry is not None:
                    self._walk_inline(out, cfg, handler_entry,
                                       case_entries, func)
            finally:
                self.ctx.in_except_tier = old_except_tier
                self.ctx.finally_stack = old_finally_stack
            if has_throw_unwind:
                self.ctx.indent_level -= 1
                inner_close = self.ctx.indent()
                out.write(f"{inner_close}}} catch (...) {{\n")
                self.ctx.indent_level += 1
                inner_catch = self.ctx.indent()
                for helper in handler_throw_finallies:
                    out.write(f"{inner_catch}this->{helper}();\n")
                out.write(f"{inner_catch}throw;\n")
                self.ctx.indent_level -= 1
                out.write(f"{inner_close}}}\n")
            self.ctx.indent_level -= 1
            out.write(f"{indent}}}")
        # Catch-all: reset sub, run inter-region finallies (innermost
        # first), run this TryRegion's own finally, re-throw.
        out.write(" catch (...) {\n")
        self.ctx.indent_level += 1
        catch_indent = self.ctx.indent()
        if yield_for_case is not None:
            self._emit_sub_reset(out, catch_indent,
                                  yield_for_case.payload,
                                  yield_for_case.suspension_index)
        for helper in extra_finallies:
            out.write(f"{catch_indent}this->{helper}();\n")
        if region.finally_helper_name is not None:
            out.write(f"{catch_indent}this->{region.finally_helper_name}();\n")
        out.write(f"{catch_indent}throw;\n")
        self.ctx.indent_level -= 1
        out.write(f"{indent}}}")

    def _resume_index_for_case(self, cfg: 'rcfg.CFG',
                                 case_entry_bb: int) -> int | None:
        y = cfg.resume_to_yield().get(case_entry_bb)
        return y.suspension_index if y is not None else None

    def _yield_at_resume(self, cfg: 'rcfg.CFG',
                          resume_bb: int) -> 'rcfg.Yield | None':
        return cfg.resume_to_yield().get(resume_bb)

    def _emit_case_body(self, out: "TextIO", cfg: 'rcfg.CFG',
                         entry_bb: int, case_entries: dict[int, _StateLabel],
                         func: TpyFunction) -> None:
        """Emit the body of a case starting at entry_bb. Begins with the
        resume step (if entry_bb is a yield-resume), then walks BBs
        inline until a terminator exits the case."""
        body_indent = self.ctx.indent()
        # Resume step.
        y = self._yield_at_resume(cfg, entry_bb)
        if y is not None:
            self._emit_resume_core(out, body_indent, y.payload,
                                    y.suspension_index, func)
            if y.payload.kind is rcfg.AwaitKind.RETURN:
                # Resume core already emitted the return.
                return
        # Walk BBs inline from entry_bb.
        self._walk_inline(out, cfg, entry_bb, case_entries, func)

    def _emit_exit_region_finallies(self, out: "TextIO", indent: str,
                                     from_regions: tuple,
                                     to_regions: tuple) -> None:
        """Emit cleanup (finally helpers + with __exit__ calls) for each
        region that exists in `from_regions` but not in `to_regions`,
        in innermost-first order. Used when control transitions from a
        deeper region stack to a shallower one (normal exit from
        try/with) -- C++ try/catch doesn't run finally on normal exit,
        so we run them explicitly here."""
        if not from_regions:
            return
        # Identify the common prefix length.
        common = 0
        while (common < len(from_regions) and common < len(to_regions)
               and from_regions[common] is to_regions[common]):
            common += 1
        exited = list(from_regions[common:])
        # Innermost first.
        for region in reversed(exited):
            if isinstance(region, rcfg.TryRegion):
                if region.finally_helper_name is not None:
                    out.write(f"{indent}this->{region.finally_helper_name}();\n")
            elif isinstance(region, rcfg.ExceptRegion):
                # Leaving an except handler normally: run the parent
                # try's finally body (Python semantics).
                if region.parent_finally is not None:
                    out.write(f"{indent}this->{region.parent_finally}();\n")
            # WithRegion handled when async-with lands.

    def _walk_inline(self, out: "TextIO", cfg: 'rcfg.CFG',
                     start_bb: int, case_entries: dict[int, _StateLabel],
                     func: TpyFunction) -> None:
        """Walk BBs starting from start_bb, emitting their statements
        and following Fall/Branch terminators inline. Stops when the
        terminator is Yield/Return/Raise/Unreachable, or when a
        Fall/Branch target is a case_entry (then emits a state
        transition)."""
        body_indent = self.ctx.indent()
        cur = start_bb
        while True:
            bb = cfg.blocks[cur]
            # Emit BB statements.
            for stmt in bb.stmts:
                self.statements.gen_stmt(out, stmt)
            t = bb.terminator
            if isinstance(t, rcfg.Yield):
                # Suspend: store state + emplace sub-future, then
                # continue to re-enter the switch at the new state.
                # The resume case's poll() will return Pending iff the
                # sub-future is genuinely not-yet-ready. Continuing
                # (rather than returning Pending unconditionally)
                # matches the original [[fallthrough]] behavior: a
                # synchronous Ready sub-future completes in one poll.
                self._emit_suspend(out, body_indent, t.payload,
                                    t.suspension_index, func)
                out.write(f"{body_indent}continue;\n")
                return
            if isinstance(t, rcfg.ReturnT):
                # gen_stmt routes a TpyReturn inside async-coro context
                # through _make_async_return, which walks the active
                # finally chain (ctx.finally_stack) before emitting the
                # Poll<T>::ready(...).
                self.statements.gen_stmt(out, t.return_stmt)
                return
            if isinstance(t, rcfg.RaiseT):
                # Emit the raise as an ordinary TpyRaise statement; the
                # finally chain is run via C++ exception unwinding.
                self.statements.gen_stmt(out, t.raise_stmt)
                return
            if isinstance(t, rcfg.Unreachable):
                self._emit_unreachable_tail(out, body_indent, func)
                return
            if isinstance(t, rcfg.Fall):
                if t.next_bb in case_entries:
                    self._emit_exit_region_finallies(
                        out, body_indent,
                        cfg.blocks[cur].region_stack,
                        cfg.blocks[t.next_bb].region_stack)
                    out.write(f"{body_indent}__state = "
                              f"{case_entries[t.next_bb].cpp_name()};\n")
                    out.write(f"{body_indent}continue;\n")
                    return
                cur = t.next_bb
                continue
            if isinstance(t, rcfg.Branch):
                cond_cpp = self.expressions.gen_expr(t.cond)
                out.write(f"{body_indent}if ({cond_cpp}) {{\n")
                self.ctx.indent_level += 1
                self._walk_inline_or_jump(out, cfg, t.then_bb, case_entries,
                                            func, from_bb=cur)
                self.ctx.indent_level -= 1
                out.write(f"{body_indent}}} else {{\n")
                self.ctx.indent_level += 1
                self._walk_inline_or_jump(out, cfg, t.else_bb, case_entries,
                                            func, from_bb=cur)
                self.ctx.indent_level -= 1
                out.write(f"{body_indent}}}\n")
                return
            raise CodeGenError(
                f"internal: unknown terminator {type(t).__name__}",
                loc=None)

    def _walk_inline_or_jump(self, out: "TextIO", cfg: 'rcfg.CFG',
                              target_bb: int,
                              case_entries: dict[int, _StateLabel],
                              func: TpyFunction,
                              from_bb: int) -> None:
        body_indent = self.ctx.indent()
        if target_bb in case_entries:
            self._emit_exit_region_finallies(
                out, body_indent,
                cfg.blocks[from_bb].region_stack,
                cfg.blocks[target_bb].region_stack)
            out.write(f"{body_indent}__state = "
                      f"{case_entries[target_bb].cpp_name()};\n")
            out.write(f"{body_indent}continue;\n")
        else:
            self._walk_inline(out, cfg, target_bb, case_entries, func)

    def _emit_unreachable_tail(self, out: "TextIO", indent: str,
                                 func: TpyFunction) -> None:
        """Tail emission for a BB whose end is statically unreachable
        (no explicit return/raise). For void async defs, emit Ready(unit);
        otherwise panic."""
        if self._is_void_return(func):
            # Walk any active finally frames before returning.
            self.statements._emit_finally_chain(out, indent)
            out.write(f"{indent}__state = S_DONE;\n")
            out.write(f"{indent}{POLL_VOID_READY_RETURN}\n")
        else:
            out.write(f"{indent}::tpy::tpy_panic(\"async def fell "
                      f"through without returning a value\");\n")

    @staticmethod
    def _sub_field_name(suspension_index: int) -> str:
        return f"__sub_{suspension_index}"

    @staticmethod
    def _is_stable_lvalue(operand: TpyExpr) -> bool:
        """True when the operand resolves to a stable lvalue: a bare
        name (frame field / local / parameter) or a chain of field
        accesses rooted at a bare name. Subscripts, calls, binops,
        conditional/comprehension expressions, etc. return temporaries
        whose address would dangle across suspensions.

        Stricter than sema's `is_lvalue` (`sema/compatibility.py`):
        subscripts (addressable in C++ but yield container-element
        temporaries across suspensions) and `TpyCoerce` (sema-only
        wrapper, doesn't reach this codegen path) are both excluded.
        """
        e = operand
        while isinstance(e, TpyFieldAccess):
            e = e.obj
        return isinstance(e, TpyName)

    def _emit_sub_reset(self, out: "TextIO", indent: str,
                        payload: 'rcfg.AwaitPayload',
                        suspension_index: int) -> None:
        sub = self._sub_field_name(suspension_index)
        if payload.mode is rcfg.AwaitMode.BORROWED:
            out.write(f"{indent}{sub} = nullptr;\n")
        elif (payload.mode is rcfg.AwaitMode.INLINE
              or payload.mode is rcfg.AwaitMode.ERASED):
            out.write(f"{indent}{sub}.reset();\n")
        else:
            raise CodeGenError(f"unknown await mode {payload.mode!r}",
                               loc=None)

    def _emit_resume_core(self, out: "TextIO", indent: str,
                          payload: 'rcfg.AwaitPayload',
                          suspension_index: int,
                          func: TpyFunction) -> None:
        """Emit the cancel check + poll + bind step at the start of a
        resume case body. Returns: caller continues with post-resume
        statements; for RETURN-kind, this function fully terminates the
        case body (walks finally chain and returns Ready)."""
        ret_cpp = self._poll_ret_cpp(func)
        sub = self._sub_field_name(suspension_index)
        out.write(f"{indent}if (__cancel_pending) {{ "
                  f"__cancel_pending = false; "
                  f"throw ::tpy::CancelledError(); }}\n")
        out.write(f"{indent}auto __r{suspension_index} = "
                  f"{sub}->__poll__(waker);\n")
        out.write(f"{indent}if (__r{suspension_index}.is_pending()) "
                  f"return {ret_cpp}::pending();\n")
        moved = f"std::move(__r{suspension_index}).value()"
        if (payload.kind is rcfg.AwaitKind.ASSIGN
                or payload.kind is rcfg.AwaitKind.VARDECL):
            target = escape_cpp_name(payload.bind_target)
            if payload.bind_target in self.ctx.generator_optional_fields:
                out.write(f"{indent}{target}.emplace({moved});\n")
            else:
                out.write(f"{indent}{target} = {moved};\n")
        elif payload.kind is rcfg.AwaitKind.RETURN:
            out.write(f"{indent}auto __ret{suspension_index} = {moved};\n")
            self._emit_sub_reset(out, indent, payload, suspension_index)
            self.statements._emit_finally_chain(out, indent)
            if self._is_void_return(func):
                out.write(f"{indent}(void)__ret{suspension_index};\n")
                out.write(f"{indent}__state = S_DONE;\n")
                out.write(f"{indent}{POLL_VOID_READY_RETURN}\n")
            else:
                out.write(f"{indent}__state = S_DONE;\n")
                out.write(f"{indent}return ::tpystd::tpy::Poll<"
                          f"{self._ret_cpp(func)}>::ready("
                          f"std::move(__ret{suspension_index}));\n")
            return
        elif payload.kind is rcfg.AwaitKind.DISCARD:
            out.write(f"{indent}(void){moved};\n")
        else:
            raise CodeGenError(f"unknown await kind {payload.kind!r}",
                               loc=None)
        self._emit_sub_reset(out, indent, payload, suspension_index)

    def _emit_suspend(self, out: "TextIO", indent: str,
                      payload: 'rcfg.AwaitPayload',
                      suspension_index: int,
                      func: TpyFunction) -> None:
        """Emit the emplace + state-advance step at a Yield terminator.

        Inline mode: emplace the sub-coro struct directly via its ctor.
        Erased mode: move the operand value into the optional field.
        Borrowed mode: store the operand's address in the pointer field.
        """
        if payload.host_stmt is not None and payload.host_stmt.loc is not None:
            self.ctx.emit_source_comment(out, payload.host_stmt.loc, indent)
        sub = self._sub_field_name(suspension_index)
        if payload.mode is rcfg.AwaitMode.INLINE:
            call = payload.operand_expr
            if not isinstance(call, TpyCall):
                raise CodeGenError(
                    "internal: inline-mode await operand is not a call",
                    loc=None)
            args = [self.expressions.gen_expr(arg) for arg in call.args]
            out.write(f"{indent}{sub}.emplace({', '.join(args)});\n")
        elif payload.mode is rcfg.AwaitMode.ERASED:
            operand_cpp = self.expressions.gen_expr(payload.operand_expr)
            out.write(f"{indent}{sub}.emplace(std::move({operand_cpp}));\n")
        elif payload.mode is rcfg.AwaitMode.BORROWED:
            operand_cpp = self.expressions.gen_expr(payload.operand_expr)
            out.write(f"{indent}{sub} = &({operand_cpp});\n")
        else:
            raise CodeGenError(f"unknown await mode {payload.mode!r}",
                               loc=None)
        out.write(f"{indent}__state = "
                  f"{_StateLabel(_StateKind.RESUME, suspension_index).cpp_name()};\n")
