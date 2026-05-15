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

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import TYPE_CHECKING


class AwaitMode(Enum):
    """How an await transition stores its sub-future in the parent frame.

    INLINE -- the awaited target is a statically-known async def; its
        coroutine struct is emplaced as a sub-future field.
    ERASED -- the awaited operand is a value awaitable (Task[T] / a
        user value type implementing Awaitable[T]); the value is
        move-stored in an std::optional sub-future field.
    BORROWED -- the awaited operand is a reference-type awaitable
        (e.g. Future[T]); the frame stores a raw pointer so multiple
        observers see the same object across suspensions.
    """
    INLINE = "inline"
    ERASED = "erased"
    BORROWED = "borrowed"


class AwaitKind(Enum):
    """How an await expression's result is consumed at its host stmt."""
    ASSIGN = "assign"
    VARDECL = "vardecl"
    RETURN = "return"
    DISCARD = "discard"

from ..parse.nodes import (
    TpyFunction, TpyAwait, TpyStmt, TpyAssign, TpyVarDecl, TpyReturn,
    TpyExprStmt, TpyName, TpyExpr, TpyTry, TpyExceptHandler, TpyCall,
    TpyFieldAccess,
)
from ..typesys import unwrap_ref_type, unwrap_own
from ..type_def_registry import is_str_type
from ..liveness import stmts_terminate
from .context import INDENT, escape_cpp_name, CodeGenError, FinallyContext


if TYPE_CHECKING:
    from io import TextIO
    from .context import CodeGenContext
    from .types import TypeMapper
    from .expressions import ExpressionGenerator
    from .statements import StatementGenerator
    from .functions import FunctionGenerator


@dataclass
class AwaitTransition:
    """A suspension point at the end of a region.

    Each TpyAwait whose parent statement is a top-level
    assign/vardecl/return/expr-stmt produces one of these. Codegen emits:
        __sub_<i>.emplace(<...>);
        __state = S_AFTER_AWAIT_<i>;
        [[fallthrough]];
    at the end of region i, and:
        if (__cancel_pending) ...;
        auto __r = __sub_<i>->__poll__(waker);
        if (__r.is_pending()) return Poll<T>::pending();
        <bind step from kind>
        __sub_<i>.reset();
    at the start of region i+1.

    `mode` is 'inline', 'erased', or 'borrowed':
    - inline: sub_field_cpp_type is `__<name>Coro`; emplace passes the
      call's args directly to the sub-coro struct's constructor.
    - erased: sub_field_cpp_type is the value awaitable type; emplace takes
      the operand expression as a value (move-stored).
    - borrowed: sub_field_cpp_type is the reference awaitable type; the
      frame stores a raw pointer to the existing object.
    """
    suspension_index: int
    mode: AwaitMode
    sub_field_cpp_type: str      # cpp type for std::optional<...> or pointer pointee
    operand_expr: TpyExpr        # the operand of `await ...`
    kind: AwaitKind
    bind_target: str | None      # variable name for assign/vardecl
    return_stmt: TpyReturn | None  # the original return for kind == 'return'
    try_handlers: list[TpyExceptHandler] | None = None
    host_stmt: TpyStmt | None = None  # the source-level statement containing the await


@dataclass
class Region:
    """A slice of the function body between suspensions (or before first / after last).

    `pre_stmts` are emitted as ordinary statements (via StatementGenerator).
    `transition` (if set) ends the region with an await suspension; the
    next region resumes from that suspension.
    """
    pre_stmts: list[TpyStmt]
    transition: AwaitTransition | None  # None for the final region


def _top_level_await_in(stmt: TpyStmt) -> TpyAwait | None:
    """Return the TpyAwait if `stmt` is one of the supported top-level shapes:
        assign with await RHS, vardecl with await init, return with await
        value, or expression-statement with bare await.
    Returns None for stmts with no top-level await; raises otherwise (callers
    catch and report as 'await position not supported in v1')."""
    value: TpyExpr | None
    if isinstance(stmt, TpyAssign):
        value = stmt.value
    elif isinstance(stmt, TpyVarDecl):
        value = stmt.init
    elif isinstance(stmt, TpyReturn):
        value = stmt.value
    elif isinstance(stmt, TpyExprStmt):
        value = stmt.expr
    else:
        # Other stmt kinds: an await nested inside is rejected by the
        # walker below.
        value = None
    if isinstance(value, TpyAwait):
        return value
    # Awaits inside nested expressions (inside an if condition, loop
    # header, with/try clauses, or arithmetic expressions) are lifted
    # to a preceding TpyVarDecl by _lift_nested_awaits before this
    # function is called. Awaits inside if/while/for/try/with bodies
    # (sub_bodies) are not yet supported and produce a clear error.
    nested = _find_any_await_in_sub_bodies(stmt)
    if nested is not None:
        raise CodeGenError(
            "await inside an if/while/for/with/try sub-body is not yet "
            "supported in v1; bind the await to a local before the "
            "control-flow statement.",
            loc=stmt.loc)
    return None


def _find_any_await_in_sub_bodies(stmt: TpyStmt) -> TpyAwait | None:
    """Look only inside sub_bodies (control-flow), not at the statement's
    own expression slots. Used after the lift pre-pass to flag awaits
    that the current codegen can't handle (loops/conditionals/etc.)."""
    if hasattr(stmt, "sub_bodies"):
        for body in stmt.sub_bodies():
            for s in body:
                r = _find_any_await(s)
                if r is not None:
                    return r
    return None


def _top_level_await_in_no_raise(stmt: TpyStmt) -> TpyAwait | None:
    """Like _top_level_await_in but doesn't raise for nested awaits.
    Used by the await-lift pre-pass which then rewrites the nested
    awaits into top-level vardecls."""
    if isinstance(stmt, TpyAssign):
        v = stmt.value
    elif isinstance(stmt, TpyVarDecl):
        v = stmt.init
    elif isinstance(stmt, TpyReturn):
        v = stmt.value
    elif isinstance(stmt, TpyExprStmt):
        v = stmt.expr
    else:
        v = None
    if isinstance(v, TpyAwait):
        return v
    return None


def _find_any_await(stmt: TpyStmt) -> TpyAwait | None:
    """Walk a statement (and its expression children, but not nested defs)
    for any TpyAwait. Used by the position-validator."""

    def walk_expr(e: TpyExpr | None) -> TpyAwait | None:
        if e is None:
            return None
        if isinstance(e, TpyAwait):
            return e
        for c in (e.children() if hasattr(e, "children") else ()):
            r = walk_expr(c)
            if r is not None:
                return r
        return None

    if hasattr(stmt, "exprs"):
        for e in stmt.exprs():
            r = walk_expr(e)
            if r is not None:
                return r
    if hasattr(stmt, "sub_bodies"):
        for body in stmt.sub_bodies():
            for s in body:
                r = _find_any_await(s)
                if r is not None:
                    return r
    return None


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
    def _wrapper_try_finally(func: TpyFunction) -> 'TpyTry | None':
        """If the function body is exactly one TpyTry with finally and no
        except handlers, return it. The wrapper-try-finally pattern
        (`async def f(): try: ... finally: cleanup()`) is the v1 supported
        shape -- the finally body becomes a __finally_top() helper invoked
        on every region's catch path and on every non-throw exit.

        Returns None if the body doesn't match (no awaits inside try, or
        more complex try patterns -- nested tries, except handlers, partial
        wrapping). Those are deferred to v1.5.
        """
        if len(func.body) != 1:
            return None
        stmt = func.body[0]
        if not isinstance(stmt, TpyTry):
            return None
        if stmt.handlers:
            return None  # except handlers around await: v1.5
        if not stmt.finally_body:
            return None  # nothing to do (could happen if user wrote try with empty finally)
        return stmt

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

    def _poll_ret_cpp(self, func: TpyFunction) -> str:
        ret = self._ret_cpp(func)
        return "::tpy::Poll<void>" if ret == "void" else f"::tpy::Poll<{ret}>"

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
        """Return the statement list that actually contains user code,
        unwrapping a single wrapping try-with-finally if present (the v1
        supported try/finally shape), then applying the await-lift pass
        so awaits embedded in expressions become top-level vardecls.

        Cached on the TpyFunction node so multiple codegen passes
        (struct emission + poll body) see the same rewrite.
        """
        cached = getattr(func, "_async_lifted_body", None)
        if cached is not None:
            return cached
        wrapper = self._wrapper_try_finally(func)
        body = wrapper.try_body if wrapper is not None else func.body
        lifted = self._lift_nested_awaits(func, body)
        func._async_lifted_body = lifted
        return lifted

    def _lift_nested_awaits(self, func: TpyFunction,
                             body: list[TpyStmt]) -> list[TpyStmt]:
        """Rewrite each statement that has awaits buried in expressions
        into a sequence of statements where every await is at top level.

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
            top_await = _top_level_await_in_no_raise(stmt)
            new_stmts = self._lift_awaits_in_stmt(func, stmt, top_await)
            out.extend(new_stmts)
        return out

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
        # Build counter from existing locals to avoid collisions.
        counter = self._next_lift_id(func)
        for await_node in lifts:
            name = f"__await_lift_{counter}"
            counter += 1
            await_t = self.ctx.get_expr_type(await_node)
            if await_t is None:
                raise CodeGenError(
                    "await result has no analyzed type (lift)", loc=stmt.loc)
            await_t = unwrap_ref_type(await_t)
            new_decl = TpyVarDecl(
                name=name, type=await_t, init=await_node, loc=stmt.loc)
            new_stmts.append(new_decl)
            # Register as hoisted local; codegen emits a frame field.
            func.generator_locals = (func.generator_locals or []) + [(name, await_t)]
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
        """Return the next free index for __await_lift_<n> names."""
        existing = 0
        for lname, _ in (func.generator_locals or []):
            if lname.startswith("__await_lift_"):
                try:
                    n = int(lname[len("__await_lift_"):])
                    if n + 1 > existing:
                        existing = n + 1
                except ValueError:
                    pass
        return existing

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

    def _try_await_position(
        self, stmt: TpyStmt,
    ) -> tuple[TpyStmt, TpyAwait, list[TpyExceptHandler]] | None:
        """Return the single supported try/except-around-await shape.

        v1 supports:
            try:
                await expr
            except E:
                ...

        The await may also be an assign/vardecl/return top-level await.
        Other statements inside the try body would need region splitting
        while preserving the source-level exception scope, so they remain
        unsupported here.
        """
        if not isinstance(stmt, TpyTry):
            return None
        await_positions: list[tuple[TpyStmt, TpyAwait]] = []
        for inner in stmt.try_body:
            await_node = _top_level_await_in_no_raise(inner)
            if await_node is not None:
                await_positions.append((inner, await_node))
            elif _find_any_await(inner) is not None:
                raise CodeGenError(
                    "await inside a try body is only supported as a single "
                    "top-level await statement in v1",
                    loc=stmt.loc)

        for body in [stmt.else_body, stmt.finally_body]:
            for inner in body:
                if _find_any_await(inner) is not None:
                    raise CodeGenError(
                        "await inside try/else/finally sub-bodies is not yet "
                        "supported in v1",
                        loc=stmt.loc)
        for handler in stmt.handlers:
            for inner in handler.body:
                if _find_any_await(inner) is not None:
                    raise CodeGenError(
                        "await inside except handlers is not yet supported in v1",
                        loc=handler.loc or stmt.loc)

        if not await_positions:
            return None
        if not stmt.handlers:
            raise CodeGenError(
                "await inside try without except handlers is not supported by "
                "this async lowering path",
                loc=stmt.loc)
        if stmt.tier != "throw":
            raise CodeGenError(
                "try/except around await currently supports throw-tier "
                "exceptions only",
                loc=stmt.loc)
        if stmt.else_body or stmt.finally_body:
            raise CodeGenError(
                "try/except around await with else/finally is not yet "
                "supported in v1",
                loc=stmt.loc)
        if len(await_positions) != 1 or len(stmt.try_body) != 1:
            raise CodeGenError(
                "try/except around await supports exactly one top-level "
                "await statement in the try body in v1",
                loc=stmt.loc)
        inner_stmt, await_node = await_positions[0]
        return inner_stmt, await_node, stmt.handlers

    def _partition_body(self, func: TpyFunction) -> list[Region]:
        """Split the body into regions delimited by top-level awaits.

        Each await produces an AwaitTransition; subsequent statements go
        into the next region. Awaits in unsupported positions raise.

        If the function body is wrapped in a single try-with-finally
        (the v1 supported shape), the unwrapped try_body is the source
        of statements; the finally is emitted as a __finally_top()
        helper called on every catch path and before every non-throw
        exit.
        """
        regions: list[Region] = []
        current_pre: list[TpyStmt] = []
        suspension_idx = 0
        for stmt in self._effective_body(func):
            try_await = self._try_await_position(stmt)
            try_handlers: list[TpyExceptHandler] | None = None
            await_stmt = stmt
            if try_await is not None:
                await_stmt, await_node, try_handlers = try_await
            else:
                await_node = _top_level_await_in(stmt)
            if await_node is None:
                current_pre.append(stmt)
                continue
            kind, bind_target, return_stmt = self._classify_await_position(await_stmt, await_node)
            # Determine inline vs erased mode from the sema annotations.
            if await_node.awaited_async_func_name is not None:
                mode = AwaitMode.INLINE
                sub_cpp = self._sub_struct_name(await_node.awaited_async_func_name)
            elif await_node.awaited_task_inner is not None:
                # Value awaitables and temporaries are consumed into the
                # frame's std::optional sub-future slot (ERASED); stable
                # non-value lvalues are stored as a pointer (BORROWED) so
                # other tasks keep observing the same object (e.g.
                # Future.set_result wakes await f). Re-awaiting a BORROWED
                # local after Ready hits the runtime "poll after Ready"
                # panic, not UAF -- the local outlives every suspension.
                operand_type = self.ctx.get_expr_type(await_node.value)
                if operand_type is None:
                    raise CodeGenError(
                        "await operand has no analyzed type", loc=stmt.loc)
                operand_inner = unwrap_own(unwrap_ref_type(operand_type))
                sub_cpp = self.types.type_to_cpp(operand_inner)
                if operand_inner.is_value_type():
                    mode = AwaitMode.ERASED
                elif self._is_stable_lvalue(await_node.value):
                    mode = AwaitMode.BORROWED
                else:
                    mode = AwaitMode.ERASED
            else:
                raise CodeGenError(
                    "await reached codegen without sema-resolved sub-future "
                    "shape (commit 3/4 invariant)", loc=stmt.loc)
            transition = AwaitTransition(
                suspension_index=suspension_idx,
                mode=mode,
                sub_field_cpp_type=sub_cpp,
                operand_expr=await_node.value,
                kind=kind,
                bind_target=bind_target,
                return_stmt=return_stmt,
                try_handlers=try_handlers,
                host_stmt=await_stmt,
            )
            regions.append(Region(pre_stmts=current_pre, transition=transition))
            current_pre = []
            suspension_idx += 1
        # Final region (statements after the last await, or everything if no awaits).
        regions.append(Region(pre_stmts=current_pre, transition=None))
        return regions

    @staticmethod
    def _classify_await_position(
        stmt: TpyStmt, await_node: TpyAwait,
    ) -> tuple[AwaitKind, str | None, TpyReturn | None]:
        """Returns (kind, bind_target, return_stmt) for the supported positions."""
        if isinstance(stmt, TpyAssign) and stmt.value is await_node:
            target = stmt.target
            if not isinstance(target, TpyName):
                raise CodeGenError(
                    "await result can only be bound to a simple name in v1; "
                    "field/index targets are not yet supported",
                    loc=stmt.loc)
            return (AwaitKind.ASSIGN, target.name, None)
        if isinstance(stmt, TpyVarDecl) and stmt.init is await_node:
            return (AwaitKind.VARDECL, stmt.name, None)
        if isinstance(stmt, TpyReturn) and stmt.value is await_node:
            return (AwaitKind.RETURN, None, stmt)
        if isinstance(stmt, TpyExprStmt) and stmt.expr is await_node:
            return (AwaitKind.DISCARD, None, None)
        raise CodeGenError(
            "internal: _classify_await_position called on unsupported stmt",
            loc=stmt.loc)

    # -- Struct definition ----------------------------------------------------

    def gen_coro_struct(self, out: "TextIO", func: TpyFunction) -> None:
        """Emit the full `__FCoro` struct definition."""
        struct_name = self.gen_struct_name(func)
        ctor_params = self._classify_params(func)
        regions = self._partition_body(func)
        transitions = [r.transition for r in regions if r.transition is not None]

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

        # Hoisted local fields (mirrors generator behavior). Value types
        # are stored bare; non-value types are std::optional<T>.
        # Unwrap any RefType the binding may have picked up (sema wraps
        # non-value-type locals in Ref by default; the underlying storage
        # is the unwrapped type).
        if func.generator_locals:
            for lname, ltype in func.generator_locals:
                ltype_inner = unwrap_ref_type(ltype)
                cpp_type = self.types.type_to_cpp(ltype_inner)
                cpp_name = escape_cpp_name(lname)
                if ltype_inner.is_value_type():
                    out.write(f"{INDENT}{cpp_type} {cpp_name};\n")
                else:
                    out.write(f"{INDENT}std::optional<{cpp_type}> {cpp_name};\n")

        # Sub-future fields: one per await. Inline mode stores the
        # generated sub-coro struct; erased mode stores a value awaitable
        # such as Task<T>. Both use std::optional<> so .reset() drops
        # in-flight state on a caught exception. Borrowed mode stores a
        # raw pointer to a reference-type awaitable such as Future<T>.
        for t in transitions:
            if t.mode is AwaitMode.BORROWED:
                out.write(f"{INDENT}{t.sub_field_cpp_type}* "
                          f"__sub_{t.suspension_index} = nullptr;\n")
            else:
                out.write(f"{INDENT}std::optional<{t.sub_field_cpp_type}> "
                          f"__sub_{t.suspension_index};\n")

        out.write(f"\n")

        # State enum: S_INITIAL, S_AFTER_AWAIT_<i> for each await, S_DONE.
        out.write(f"{INDENT}enum : int32_t {{\n")
        out.write(f"{INDENT}{INDENT}S_INITIAL = 0,\n")
        for t in transitions:
            out.write(f"{INDENT}{INDENT}S_AFTER_AWAIT_{t.suspension_index} = "
                      f"{t.suspension_index + 1},\n")
        out.write(f"{INDENT}{INDENT}S_DONE = {len(transitions) + 1},\n")
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

        # __poll__() forward declaration -- the structural-Awaitable
        # method (matches `tpy.coro.Awaitable.__poll__`).
        out.write(f"{INDENT}{self._poll_ret_cpp(func)} __poll__(::tpy::Waker waker);\n")

        # __finally_top() forward declaration -- v1's wrapper-try-finally
        # support emits the source-level `finally:` body as a private
        # member; each region's catch wrapper and the non-throw-exit path
        # call it before re-throwing or returning.
        if self._wrapper_try_finally(func) is not None:
            out.write(f"{INDENT}void __finally_top();\n")

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
        """Emit the __finally_top() member body for the wrapper try-finally
        shape. No-op (does not emit) if there's no wrapper try.
        """
        wrapper = self._wrapper_try_finally(func)
        if wrapper is None:
            return
        struct_name = self.gen_struct_name(func)
        self._emit_template_header(out, func)
        out.write(f"void {struct_name}::__finally_top() {{\n")

        # The finally body emission goes through the same field-rewrite
        # path the poll() body uses: params + hoisted locals are frame
        # fields. Set up the same context flags. Do NOT set
        # in_async_coro_body (return inside a finally is illegal in TPy
        # async semantics; if the user wrote one it would generate
        # broken code -- v1.5 will reject this case in sema).
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
            self.ctx.indent_level = 1
            for stmt in wrapper.finally_body:
                self.statements.gen_stmt(out, stmt)
        finally:
            self.ctx.indent_level = 0
            self.ctx.in_generator_body = old_in_gen
            self.ctx.generator_field_names = old_field_names
            self.ctx.generator_optional_fields = old_optional_fields
            self.ctx.generator_for_loop_info = old_for_info
            self.ctx.generator_self_ref = old_self_ref

        out.write(f"}}\n")

    def gen_coro_poll_def(self, out: "TextIO", func: TpyFunction) -> None:
        """Emit the `poll()` method body in the .cpp file (or inline-in-hpp
        for templates -- the caller handles placement).
        """
        struct_name = self.gen_struct_name(func)
        regions = self._partition_body(func)
        transitions = [r.transition for r in regions if r.transition is not None]

        self.ctx.emit_source_comment(out, func.loc)
        self._emit_template_header(out, func)
        out.write(f"{self._poll_ret_cpp(func)} {struct_name}::__poll__(::tpy::Waker waker) {{\n")
        if not transitions:
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
            # Emit switch + each region's case body.
            self._emit_switch_body(out, func, regions)
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

    def _emit_switch_body(self, out: "TextIO", func: TpyFunction,
                          regions: list[Region]) -> None:
        """Emit the switch over __state with case bodies for each region."""
        self.ctx.indent_level = 1
        inner = self.ctx.indent()
        wrapper_try = self._wrapper_try_finally(func)
        all_transitions = [
            r.transition for r in regions if r.transition is not None
        ]

        # Push a finally frame for the wrapper try (if any) so that
        # TpyReturn inside the body emits __finally_top() before the
        # actual return via the existing _emit_finally_chain path. The
        # FinallyContext's emit callback writes a literal `this->__finally_top();`
        # call; structurally identical to PR 1's sync try/finally.
        if wrapper_try is not None:
            def _emit_finally_top(o: "TextIO", ind: str) -> None:
                o.write(f"{ind}this->__finally_top();\n")

            self._wrapper_finally_ctx = FinallyContext(
                emit_finally=_emit_finally_top,
                terminates=False,
                loop_depth=0,
            )
            self.ctx.finally_stack.append(self._wrapper_finally_ctx)

        out.write(f"{inner}switch (__state) {{\n")
        self.ctx.indent_level = 2
        case_indent = self.ctx.indent()

        for i, region in enumerate(regions):
            if i == 0:
                state_name = "S_INITIAL"
            else:
                prev_idx = regions[i - 1].transition.suspension_index
                state_name = f"S_AFTER_AWAIT_{prev_idx}"
            out.write(f"{inner}case {state_name}: {{\n")
            self.ctx.indent_level = 3
            body_indent = self.ctx.indent()

            # If the function is wrapped in try/finally, each case body
            # is itself wrapped in C++ try/catch so an exception thrown
            # during the resume / region body is caught, sub-future
            # storage is reset, the source-level finally runs, and the
            # exception re-throws.
            if wrapper_try is not None:
                out.write(f"{body_indent}try {{\n")
                self.ctx.indent_level = 4
                body_indent = self.ctx.indent()

            # Resume step (only for non-initial regions).
            if i > 0:
                prev_t = regions[i - 1].transition
                self._emit_resume(out, body_indent, prev_t, func)

            # Region pre-stmts.
            for stmt in region.pre_stmts:
                self.statements.gen_stmt(out, stmt)

            # Transition or fall-through to S_DONE.
            if region.transition is not None:
                self._emit_suspend(out, body_indent, region.transition, func)
            else:
                # Final region. If the user's pre_stmts already terminate
                # (last stmt is `return` / `raise` / etc.), no fallback
                # is needed -- the return was already emitted. Otherwise
                # emit `Poll<void>::ready()` for void coros, or a panic
                # for non-void coros with a missing return.
                if not stmts_terminate(region.pre_stmts):
                    ret_cpp = self._ret_cpp(func)
                    if ret_cpp == "void":
                        if wrapper_try is not None:
                            out.write(f"{body_indent}this->__finally_top();\n")
                        out.write(f"{body_indent}__state = S_DONE;\n")
                        out.write(f"{body_indent}return ::tpy::Poll<void>::ready();\n")
                    else:
                        out.write(f"{body_indent}::tpy::tpy_panic(\"async def fell "
                                  f"through without returning a value\");\n")

            if wrapper_try is not None:
                # Close the per-case try block + emit catch wrapper.
                self.ctx.indent_level = 3
                body_indent = self.ctx.indent()
                out.write(f"{body_indent}}} catch (...) {{\n")
                self.ctx.indent_level = 4
                catch_indent = self.ctx.indent()
                # Reset all sub-future fields (only those that exist on
                # the struct) -- conservative; std::optional::reset() on an
                # empty optional is a safe no-op.
                for t in all_transitions:
                    self._emit_sub_reset(out, catch_indent, t)
                out.write(f"{catch_indent}this->__finally_top();\n")
                out.write(f"{catch_indent}throw;\n")
                self.ctx.indent_level = 3
                body_indent = self.ctx.indent()
                out.write(f"{body_indent}}}\n")

            # Close the case body block + fallthrough/break.
            self.ctx.indent_level = 2
            out.write(f"{case_indent}}}\n")
            if region.transition is not None:
                out.write(f"{case_indent}[[fallthrough]];\n")

        # S_DONE case.
        out.write(f"{inner}case S_DONE: ::tpy::tpy_panic(\"poll after Ready\");\n")
        out.write(f"{inner}}}\n")
        out.write(f"{inner}__builtin_unreachable();\n")
        self.ctx.indent_level = 0

        if wrapper_try is not None:
            self.ctx.finally_stack.pop()

    @staticmethod
    def _sub_field_name(t: AwaitTransition) -> str:
        return f"__sub_{t.suspension_index}"

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
                        t: AwaitTransition) -> None:
        sub = self._sub_field_name(t)
        if t.mode is AwaitMode.BORROWED:
            out.write(f"{indent}{sub} = nullptr;\n")
        elif t.mode is AwaitMode.INLINE or t.mode is AwaitMode.ERASED:
            out.write(f"{indent}{sub}.reset();\n")
        else:
            raise CodeGenError(f"unknown await mode {t.mode!r}", loc=None)

    def _emit_resume(self, out: "TextIO", indent: str, t: AwaitTransition,
                     func: TpyFunction) -> None:
        """Emit the cancel check + poll + bind step for resuming after the
        await numbered `t.suspension_index`."""
        if t.try_handlers:
            out.write(f"{indent}try {{\n")
            self._emit_resume_core(out, indent + INDENT, t, func)
            out.write(f"{indent}}}")
            self._emit_resume_handlers(out, indent, t)
        else:
            self._emit_resume_core(out, indent, t, func)

    def _emit_resume_core(self, out: "TextIO", indent: str,
                          t: AwaitTransition, func: TpyFunction) -> None:
        ret_cpp = self._poll_ret_cpp(func)
        sub = self._sub_field_name(t)
        out.write(f"{indent}if (__cancel_pending) {{ "
                  f"__cancel_pending = false; "
                  f"throw ::tpy::CancelledError(); }}\n")
        out.write(f"{indent}auto __r{t.suspension_index} = {sub}->__poll__(waker);\n")
        out.write(f"{indent}if (__r{t.suspension_index}.is_pending()) "
                  f"return {ret_cpp}::pending();\n")
        # Bind result based on the kind.
        moved = f"std::move(__r{t.suspension_index}).value()"
        if t.kind is AwaitKind.ASSIGN or t.kind is AwaitKind.VARDECL:
            target = escape_cpp_name(t.bind_target)
            # Hoisted locals are frame fields; assignment is just `target = ...`
            # (in_generator_body field-rewrite handles bare-name lookup).
            # For optional-typed fields, we emplace; for value, plain assign.
            if t.bind_target in self.ctx.generator_optional_fields:
                out.write(f"{indent}{target}.emplace({moved});\n")
            else:
                out.write(f"{indent}{target} = {moved};\n")
        elif t.kind is AwaitKind.RETURN:
            ret_inner = self._ret_cpp(func)
            # `return await sub()` must walk any active finally chain
            # before emitting the actual return -- the wrapper-try
            # helper is on ctx.finally_stack at this point. Bind the
            # value to a temp first (the user's expression doesn't
            # exist after the lift) then run the chain, then return.
            out.write(f"{indent}auto __ret{t.suspension_index} = {moved};\n")
            self._emit_sub_reset(out, indent, t)
            # Walk finally chain (mutates and restores ctx.finally_stack).
            # Mirrors statements.py's _make_async_return: emits
            # __finally_top() etc. before the actual return.
            self.statements._emit_finally_chain(out, indent)
            if ret_inner == "void":
                out.write(f"{indent}(void)__ret{t.suspension_index};\n")
                out.write(f"{indent}__state = S_DONE;\n")
                out.write(f"{indent}return ::tpy::Poll<void>::ready();\n")
            else:
                out.write(f"{indent}__state = S_DONE;\n")
                out.write(f"{indent}return ::tpy::Poll<{ret_inner}>::ready("
                          f"std::move(__ret{t.suspension_index}));\n")
            return  # No further reset -- we already reset above.
        elif t.kind is AwaitKind.DISCARD:
            out.write(f"{indent}(void){moved};\n")
        else:
            raise CodeGenError(f"unknown await kind {t.kind!r}", loc=None)
        self._emit_sub_reset(out, indent, t)

    def _emit_resume_handlers(self, out: "TextIO", indent: str,
                              t: AwaitTransition) -> None:
        handlers = t.try_handlers or []
        base_level = len(indent) // len(INDENT)
        old_indent_level = self.ctx.indent_level
        old_except_tier = self.ctx.in_except_tier
        try:
            for handler in handlers:
                self.statements._emit_except_handler_header(out, handler)
                self.ctx.indent_level = base_level + 1
                catch_indent = self.ctx.indent()
                self._emit_sub_reset(out, catch_indent, t)
                self.ctx.in_except_tier = "throw"
                for stmt in handler.body:
                    self.statements.gen_stmt(out, stmt)
                self.ctx.in_except_tier = old_except_tier
                self.ctx.indent_level = base_level
                out.write(f"{indent}}}")
            out.write("\n")
        finally:
            self.ctx.indent_level = old_indent_level
            self.ctx.in_except_tier = old_except_tier

    def _emit_suspend(self, out: "TextIO", indent: str, t: AwaitTransition,
                      func: TpyFunction) -> None:
        """Emit the emplace + state-advance step at the end of a region.

        Inline mode: the operand is `TpyCall(async_def, args)`. We emplace
        the sub-coro struct directly via its ctor (skipping the factory
        function so RVO/NRVO doesn't matter -- emplace constructs in place).

        Erased mode: the operand is any expression of type Task[T]. We
        emplace by moving the operand into the optional field.
        """
        if t.host_stmt is not None and t.host_stmt.loc is not None:
            self.ctx.emit_source_comment(out, t.host_stmt.loc, indent)
        if t.mode is AwaitMode.INLINE:
            call = t.operand_expr
            if not isinstance(call, TpyCall):
                raise CodeGenError(
                    "internal: inline-mode await operand is not a call",
                    loc=None)
            args = [self.expressions.gen_expr(arg) for arg in call.args]
            out.write(f"{indent}__sub_{t.suspension_index}.emplace("
                      f"{', '.join(args)});\n")
        elif t.mode is AwaitMode.ERASED:
            operand_cpp = self.expressions.gen_expr(t.operand_expr)
            out.write(f"{indent}__sub_{t.suspension_index}.emplace(std::move("
                      f"{operand_cpp}));\n")
        elif t.mode is AwaitMode.BORROWED:
            # Borrow mode is only selected when the operand was already
            # determined to be a stable lvalue (`_is_stable_lvalue`).
            # Temporaries route to ERASED mode and are stored by value
            # in the frame's std::optional sub-future slot.
            operand_cpp = self.expressions.gen_expr(t.operand_expr)
            out.write(f"{indent}__sub_{t.suspension_index} = &({operand_cpp});\n")
        else:
            raise CodeGenError(f"unknown await mode {t.mode!r}", loc=None)
        out.write(f"{indent}__state = S_AFTER_AWAIT_{t.suspension_index};\n")
