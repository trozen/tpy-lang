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

import contextlib
import copy
from dataclasses import dataclass, fields, is_dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from ..namespace import Namespace
from ..parse.nodes import (
    TpyFunction, TpyAwait, TpyStmt, TpyAssign, TpyVarDecl, TpyReturn,
    TpyExprStmt, TpyName, TpyExpr, TpyTry, TpyExceptHandler, TpyCall,
    TpyMethodCall, TpyFieldAccess, TpyForEach, TpyWith,
    TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBoolLiteral,
    TpyNoneLiteral, TpyCoerce,
    is_stable_address_lvalue,
)
from ..typesys import NominalType, OptionalType, TypeParamRef, unwrap_readonly, unwrap_ref_type, unwrap_own, VoidType
from ..type_def_registry import is_str_type, is_str_category
from .context import INDENT, escape_cpp_name, CodeGenError, FinallyContext, module_to_cpp_namespace, qualified_cpp_name
from . import resumable_cfg as rcfg


if TYPE_CHECKING:
    from io import TextIO
    from .context import CodeGenContext
    from .types import TypeMapper
    from .expressions import ExpressionGenerator
    from .statements import StatementGenerator
    from .functions import FunctionGenerator


class _CoroParamKind(IntEnum):
    """Storage form for a coro-struct captured param.

    REF: non-value type bound by reference; field is `T&`, ctor takes `T&`,
        init binds the reference. Caller must keep the source alive across
        polls.
    VALUE: value type (or string view) stored by value; field is `T`,
        ctor takes `T x_` and the init moves into the field.
    POINTER: pointer-form Optional[NonValue] param; field is `T*` (or
        `const T*`), ctor takes the same. Init is a direct copy -- raw
        pointers are trivially copyable, so std::move would just add
        noise.
    TYPE_PARAM: TypeParamRef param whose value-vs-reference resolution
        happens at instantiation; field is `::tpy::val_or_ref_t<T>` (T for
        value types, T& for object types), ctor takes
        `::tpy::param_val_or_ref_t<T>` (const T& or T&), init directly
        binds/copies. No std::move (the param is already a reference).
    STATIC_PROTOCOL: static-protocol-typed param (e.g. `Own[Awaitable[T]]`)
        whose concrete type is deduced as an extra template arg `T_<pname>`
        with a concept constraint -- mirrors `gen_params_with_protocols`
        in `functions.py`. Field stores by value (the concrete deduced
        type); ctor takes `T_<pname>&&` (forwarding ref) and moves in;
        the factory forwards the same way.
    """
    REF = 0
    VALUE = 1
    POINTER = 2
    TYPE_PARAM = 3
    STATIC_PROTOCOL = 4


@dataclass(frozen=True)
class _CoroParam:
    cpp_name: str
    field_type: str  # type spelling for the frame-field declaration
    ctor_param_type: str  # type spelling for the constructor's parameter
    kind: _CoroParamKind

    def field_decl(self) -> str:
        if self.kind is _CoroParamKind.REF:
            return f"{self.field_type}& {self.cpp_name}"
        return f"{self.field_type} {self.cpp_name}"

    def factory_param_decl(self) -> str:
        # Factory function signature param: same C++ type as the ctor's
        # param (matters for TYPE_PARAM, where it's `param_val_or_ref_t<T>`
        # rather than the field's `val_or_ref_t<T>`), but with the bare
        # name -- the factory body forwards by bare name.
        if self.kind is _CoroParamKind.REF:
            return f"{self.ctor_param_type}& {self.cpp_name}"
        if self.kind is _CoroParamKind.STATIC_PROTOCOL:
            return f"{self.ctor_param_type}&& {self.cpp_name}"
        return f"{self.ctor_param_type} {self.cpp_name}"

    def ctor_param_decl(self) -> str:
        if self.kind is _CoroParamKind.REF:
            return f"{self.ctor_param_type}& {self.cpp_name}"
        if self.kind is _CoroParamKind.STATIC_PROTOCOL:
            return f"{self.ctor_param_type}&& {self.cpp_name}_"
        # VALUE / POINTER / TYPE_PARAM: `_` suffix disambiguates from the
        # field name in the init list.
        return f"{self.ctor_param_type} {self.cpp_name}_"

    def ctor_init(self) -> str:
        if self.kind is _CoroParamKind.REF:
            return f"{self.cpp_name}({self.cpp_name})"
        if self.kind in (_CoroParamKind.POINTER, _CoroParamKind.TYPE_PARAM):
            # POINTER: raw pointer, trivially copyable -- std::move is noise.
            # TYPE_PARAM: `param_val_or_ref_t<T>` is already a reference type;
            # std::move on it yields an rvalue that won't bind to the field
            # type for non-value Ts. Direct bind/copy is uniformly correct.
            return f"{self.cpp_name}({self.cpp_name}_)"
        return f"{self.cpp_name}(std::move({self.cpp_name}_))"


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


def _same_elements(a: list, b: list) -> bool:
    """True iff `a` and `b` have identical length and element-by-element
    identity. Used by the async lift pre-pass to decide whether a
    sub-body actually changed -- distinct list objects with all-`is`
    elements are treated as unchanged so the parent stmt's id stays
    stable."""
    return len(a) == len(b) and all(x is y for x, y in zip(a, b))


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
    def gen_struct_name(func: TpyFunction, record_name: str | None = None) -> str:
        """Coro struct name: `__coro_<funcname>` for free async defs,
        `__coro_<Record>_<funcname>` for async methods. Mirrors
        `GeneratorCodegen.gen_struct_name` so async-method codegen
        parallels generator-method codegen."""
        return AsyncCoroCodegen._sub_struct_name(func.name, record_name)

    @staticmethod
    def _sub_struct_name(name: str, owner_record: str | None = None) -> str:
        """Coro struct name from raw strings: `__coro_<name>` for free
        async defs, `__coro_<Owner>_<name>` for methods. Single source
        of truth for `gen_struct_name`, the await payload factory (sub-
        coroutine of a statically-resolved await), and the async-with
        prescan (which only has the CM's `NominalType.name`)."""
        if owner_record:
            return (f"__coro_{escape_cpp_name(owner_record)}_"
                    f"{escape_cpp_name(name)}")
        return f"__coro_{escape_cpp_name(name)}"

    def _classify_params(self, func: TpyFunction,
                          record_name: str | None = None
                          ) -> list[_CoroParam]:
        """Classify async-def params for the coro struct field/ctor shape.

        Returns a list of `_CoroParam` records, one per captured param.
        When `record_name` is set, prepends `__self: <Record>&` (or
        `const <Record>&` for @readonly methods) so async methods
        capture their receiver -- parallels `GeneratorCodegen`
        self-capture.

        Generic params (`T` as TypeParamRef) use the
        `param_val_or_ref_t<T>` / `val_or_ref_t<T>` trait so each
        instantiation picks the right value-vs-reference shape -- a
        value-typed T (e.g. Int32) stores by value (so literal /
        rvalue call-site args don't dangle), while an object-typed T
        stores by reference (matching Python semantics and the
        non-template ref path).

        v1 conservative rule for str: passes by string_view (caller's
        storage, same lifetime model as generators -- coros that
        escape via Task will need to copy out, but until that lands
        the borrow holds across await boundaries within a single
        asyncio.run).
        """
        out: list[_CoroParam] = []
        if record_name:
            cpp_record = escape_cpp_name(record_name)
            const_prefix = "const " if func.is_readonly else ""
            recv_type = f"{const_prefix}{cpp_record}"
            out.append(_CoroParam(
                cpp_name="__self",
                field_type=recv_type,
                ctor_param_type=recv_type,
                kind=_CoroParamKind.REF,
            ))
        for pname, ptype in func.params:
            cpp_name = escape_cpp_name(pname)
            ptype_inner = unwrap_ref_type(ptype)
            actual = unwrap_readonly(ptype_inner)
            if is_str_type(ptype_inner):
                out.append(_CoroParam(
                    cpp_name=cpp_name,
                    field_type="std::string_view",
                    ctor_param_type="std::string_view",
                    kind=_CoroParamKind.VALUE,
                ))
            elif isinstance(ptype_inner, TypeParamRef):
                # to_cpp_return / to_cpp_param_type already encode the
                # val_or_ref_t<T> / param_val_or_ref_t<T> traits (and
                # collapse to std::size_t for INT-kind params).
                out.append(_CoroParam(
                    cpp_name=cpp_name,
                    field_type=ptype_inner.to_cpp_return(),
                    ctor_param_type=ptype_inner.to_cpp_param_type(),
                    kind=_CoroParamKind.TYPE_PARAM,
                ))
            elif self.functions.protocols.is_static_protocol_param(ptype):
                # Static-protocol param (e.g. `Own[Awaitable[T]]`): the
                # concrete operand type is deduced as an extra template
                # arg `T_<pname>` with a concept constraint, declared in
                # `_emit_template_header`. Field stores by value;
                # ctor/factory forward via T_<pname>&&. Checked before
                # the OptionalType-pointer-repr branch below so
                # `Own[Awaitable[T]] | None` doesn't get mis-routed to
                # POINTER -- `_protocol_template_parts` will reject the
                # nullable shape with a clear diagnostic.
                #
                # `T_{pname}` (raw, not escaped) keeps the template-arg
                # name aligned with `_protocol_template_parts` (which
                # declares the template arg) -- `_struct_name_templated`
                # reads the same field back to spell instantiations.
                # Using `cpp_name` here would diverge when `pname`
                # collides with a C++ keyword (e.g. `class` -> `class_`).
                template_arg = f"T_{pname}"
                out.append(_CoroParam(
                    cpp_name=cpp_name,
                    field_type=template_arg,
                    ctor_param_type=template_arg,
                    kind=_CoroParamKind.STATIC_PROTOCOL,
                ))
            elif isinstance(actual, OptionalType) and actual.uses_pointer_repr():
                cpp_type = ptype_inner.to_cpp_param_type()
                out.append(_CoroParam(
                    cpp_name=cpp_name,
                    field_type=cpp_type,
                    ctor_param_type=cpp_type,
                    kind=_CoroParamKind.POINTER,
                ))
            else:
                cpp_type = self.types.type_to_cpp(ptype_inner)
                if ptype_inner.is_value_type():
                    out.append(_CoroParam(
                        cpp_name=cpp_name,
                        field_type=cpp_type,
                        ctor_param_type=cpp_type,
                        kind=_CoroParamKind.VALUE,
                    ))
                else:
                    out.append(_CoroParam(
                        cpp_name=cpp_name,
                        field_type=cpp_type,
                        ctor_param_type=cpp_type,
                        kind=_CoroParamKind.REF,
                    ))
        return out

    def _protocol_template_parts(self, func: TpyFunction) -> list[str]:
        """Return template-header parts for any static-protocol-typed
        params on `func`. Each part has the form `<Concept> T_<pname>`
        (or `<Concept><type_args> T_<pname>` when the protocol is
        generic). Mirrors `gen_combined_template_header` in protocols.py
        for the single-required-protocol case; multi-protocol / nullable
        shapes are deferred until a concrete need surfaces.
        """
        parts: list[str] = []
        for pname, ptype in func.params:
            if not self.functions.protocols.is_static_protocol_param(ptype):
                continue
            infos = self.functions.protocols.get_all_protocol_params(
                [(pname, ptype)])
            if not infos:
                continue
            info = infos[0]
            if len(info.protocols) != 1 or info.has_none:
                raise CodeGenError(
                    f"async def param {pname!r}: multi-protocol or "
                    "optional-protocol shape is not yet supported in "
                    "async-def coro codegen (only single required "
                    "protocols like `Own[Awaitable[T]]`)",
                    loc=func.loc)
            proto = info.protocols[0]
            concept_name = self.functions.protocols.get_concept_name(proto)
            if proto.type_args:
                targs = ", ".join(t.to_cpp() for t in proto.type_args)
                parts.append(f"{concept_name}<{targs}> T_{pname}")
            else:
                parts.append(f"{concept_name} T_{pname}")
        return parts

    def _emit_template_header(self, out: "TextIO", func: TpyFunction,
                                *, indent: str = "") -> bool:
        proto_parts = self._protocol_template_parts(func)
        if not func.type_params and not proto_parts:
            return False
        parts = [f"typename {tp}" for tp in func.type_params]
        parts.extend(proto_parts)
        out.write(f"{indent}template <{', '.join(parts)}>\n")
        return True

    def _struct_name_templated(self, func: TpyFunction,
                                record_name: str | None = None) -> str:
        """Return the coro struct name suffixed with `<T1, T2, ...>` when the
        function is generic, else the bare name. Use this whenever the
        struct name appears in a type-name position (return types,
        out-of-line method qualifiers, parameter types) -- C++ rejects
        the injected-class-name there. The bare `gen_struct_name` is
        still correct inside the struct body (constructors) and for
        forward decls (`struct X;`).

        For functions whose params include static protocols, the
        per-param `T_<pname>` template args are appended after the
        explicit `[T1, T2, ...]` type-param list, in param order.
        """
        bare = AsyncCoroCodegen.gen_struct_name(func, record_name)
        extras = [p.field_type for p in self._classify_params(func, record_name)
                  if p.kind is _CoroParamKind.STATIC_PROTOCOL]
        all_args = list(func.type_params) + extras
        if not all_args:
            return bare
        return f"{bare}<{', '.join(all_args)}>"

    def _emit_params_decl(self, func: TpyFunction) -> str:
        return ", ".join(p.factory_param_decl() for p in self._classify_params(func))

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

    def gen_coro_forward_decl(self, out: "TextIO", func: TpyFunction,
                              record_name: str | None = None) -> bool:
        struct_name = self.gen_struct_name(func, record_name)
        self._emit_template_header(out, func)
        out.write(f"struct {struct_name};\n")
        return True

    def gen_factory_forward_decl(self, out: "TextIO", func: TpyFunction) -> bool:
        return_type_name = self._struct_name_templated(func)
        self._emit_template_header(out, func)
        params = self._emit_params_decl(func)
        out.write(f"{return_type_name} {escape_cpp_name(func.name)}({params});\n")
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
        # Preserve the original stmt object (and its id) when sub-body
        # lifting produced no actual changes -- any analyzer-side
        # id(stmt)-keyed dict (e.g. if_branch_decls) would otherwise be
        # orphaned by a spurious clone.
        replacements: dict[str, list[TpyStmt]] = {}
        for f in fields(stmt):
            v = getattr(stmt, f.name, None)
            if isinstance(v, list) and v and isinstance(v[0], TpyStmt):
                lifted = self._lift_nested_awaits(func, v)
                if not _same_elements(lifted, v):
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
                if not _same_elements(lifted_body, h.body):
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

    def gen_coro_struct(self, out: "TextIO", func: TpyFunction,
                         record_name: str | None = None) -> None:
        """Emit the full `__FCoro` struct definition. When `record_name`
        is given, the struct captures `__self: <Record>&` and the struct
        name is `__coro_<Record>_<func>` (mirrors generator-method
        codegen).
        """
        struct_name = self.gen_struct_name(func, record_name)
        ctor_params = self._classify_params(func, record_name)
        cfg = self._build_cfg(func)
        yields = cfg.yield_sites

        label = f"{record_name}.{func.name}" if record_name else func.name
        out.write(f"// Async coroutine: {label}\n")
        self._emit_template_header(out, func)
        out.write(f"struct {struct_name} {{\n")

        # State + cancel flag
        out.write(f"{INDENT}int32_t __state;\n")
        out.write(f"{INDENT}bool __cancel_pending;\n")

        # Captured param fields
        for p in ctor_params:
            out.write(f"{INDENT}{p.field_decl()};\n")

        # Hoisted local fields (mirrors generator behavior).
        if func.generator_locals:
            owning_str = getattr(
                func, "_with_owning_str_targets", set())
            for lname, ltype in func.generator_locals:
                ltype_inner = unwrap_ref_type(ltype)
                cpp_name = escape_cpp_name(lname)
                if lname in owning_str:
                    # `with X() as label:` -- `__enter__` returns by
                    # value; storing the view across suspensions
                    # would dangle. Use owning storage. See
                    # `_prescan_with_stmts`.
                    out.write(f"{INDENT}std::string {cpp_name};\n")
                elif ltype_inner.is_value_type():
                    cpp_type = self.types.type_to_cpp(ltype_inner)
                    out.write(f"{INDENT}{cpp_type} {cpp_name};\n")
                elif (isinstance(ltype_inner, OptionalType)
                        and ltype_inner.uses_pointer_repr()):
                    # Pointer-repr Optional: bare `T* = nullptr` aliases
                    # the source and uses nullptr as both "uninitialized"
                    # and "None"; no outer `std::optional<...>` wrap.
                    inner_cpp = self.types.type_to_cpp(ltype_inner.inner)
                    out.write(f"{INDENT}{inner_cpp}* {cpp_name} = nullptr;\n")
                else:
                    cpp_type = self.types.type_to_cpp(ltype_inner)
                    out.write(f"{INDENT}::tpy::frame_slot<{cpp_type}> {cpp_name};\n")

        # Synthetic fields for CFG-decomposed for-loops: one
        # iterator + one __next__-result slot per for-with-await,
        # stored as std::optional so the C++ type is default-constructible.
        for fname, ftype in getattr(func, "_async_for_fields", ()) or ():
            out.write(f"{INDENT}std::optional<{ftype}> {fname};\n")

        # Context-manager fields for CFG-decomposed `with`-with-await
        # bodies. One std::optional<T> per WithItem.
        for fname, ftype in getattr(func, "_with_fields", ()) or ():
            out.write(f"{INDENT}std::optional<{ftype}> {fname};\n")

        # In-flight exception slots for CFG-decomposed try-finally-with-
        # await bodies. std::exception_ptr default-constructs
        # to null; the catch arm sets it via std::current_exception().
        # bool pending-return flags need explicit init: NSDMI
        # = false. Value slots default-init via their own type's ctor.
        for fname, ftype in getattr(func, "_async_try_finally_fields", ()) or ():
            if ftype == "bool":
                out.write(f"{INDENT}{ftype} {fname} = false;\n")
            else:
                out.write(f"{INDENT}{ftype} {fname};\n")

        # Sub-future fields: one per Yield (suspension_index = field
        # ordinal). Inline mode: optional<__<name>Coro>; Erased: optional
        # of the value awaitable; Borrowed: raw pointer. Async-with's
        # synthetic yields override sub_field_cpp_type via the
        # _async_with_struct_names map (the CM's __aenter__/__aexit__
        # coro struct, computed at prescan time).
        struct_names = getattr(func, "_async_with_struct_names", {}) or {}
        for_struct_names = getattr(func, "_async_for_struct_names", {}) or {}
        for y in yields:
            p = y.payload
            sub_cpp = p.sub_field_cpp_type
            if p.async_with_kind is not None and p.async_with_ctx_n is not None:
                entry = struct_names.get(p.async_with_ctx_n)
                if entry is not None:
                    sub_cpp = entry[0] if p.async_with_kind is rcfg.AsyncWithKind.AENTER else entry[1]
            elif p.async_for_uid is not None:
                entry = for_struct_names.get(p.async_for_uid)
                if entry is not None:
                    sub_cpp = entry
            if p.mode is rcfg.AwaitMode.BORROWED:
                out.write(f"{INDENT}{sub_cpp}* "
                          f"__sub_{y.suspension_index} = nullptr;\n")
            else:
                out.write(f"{INDENT}std::optional<{sub_cpp}> "
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
        ctor_param_list = ", ".join(p.ctor_param_decl() for p in ctor_params)
        init_parts = ["__state(S_INITIAL)", "__cancel_pending(false)"]
        init_parts.extend(p.ctor_init() for p in ctor_params)
        out.write(f"{INDENT}{struct_name}({ctor_param_list})\n")
        out.write(f"{INDENT}{INDENT}: {', '.join(init_parts)} {{}}\n\n")

        # __poll__() forward declaration.
        out.write(f"{INDENT}{self._poll_ret_cpp(func)} __poll__(::tpystd::coro::Waker waker);\n")
        out.write(f"{INDENT}void cancel() {{ __cancel_pending = true; }}\n")

        # Finally-helper forward declarations: one per TryRegion with a
        # finally body.
        for helper_name, _body in cfg.finally_helpers:
            out.write(f"{INDENT}void {helper_name}();\n")

        repr_label = (f"{record_name}.{func.name}" if record_name
                      else func.name)
        param_struct_name = self._struct_name_templated(func, record_name)
        out.write(f"\n{INDENT}friend std::ostream& operator<<("
                  f"std::ostream& os, const {param_struct_name}&) {{\n")
        out.write(f"{INDENT}{INDENT}return os << \"<coroutine {repr_label}>\";\n")
        out.write(f"{INDENT}}}\n")
        out.write(f"}};\n")

    # -- Factory function -----------------------------------------------------

    def gen_factory(self, out: "TextIO", func: TpyFunction) -> None:
        """Emit the factory function: `__FCoro f(args) { return __FCoro(args); }`.

        Uses the templated struct name explicitly so zero-param generic
        async defs (no ctor args for CTAD to deduce T from) compile."""
        struct_name = self._struct_name_templated(func)
        self.ctx.emit_source_comment(out, func.loc)
        self._emit_template_header(out, func)
        params = self._emit_params_decl(func)
        out.write(f"{struct_name} {escape_cpp_name(func.name)}({params}) {{\n")
        args = self._factory_args_forwarded(func)
        out.write(f"{INDENT}return {struct_name}({args});\n")
        out.write(f"}}\n")

    def _factory_args_forwarded(
            self, func: TpyFunction,
            *, receiver: tuple[str, str] | None = None) -> str:
        """Format the arg list for a coro-struct factory call.

        Static-protocol params (`Own[Awaitable[T]]` etc.) take a
        forwarding-ref `T_<pname>&&` -- which, inside the factory body,
        is an lvalue -- so the call to the struct ctor (also `T_<pname>&&`)
        must wrap the name in `std::move(...)` to bind. Other kinds pass
        by bare name.

        `receiver=(record_name, recv_expr)` supports async methods: the
        receiver is prepended to the arg list as `recv_expr` (typically
        `"*this"`), and `__self` is filtered out of the classified
        params so it isn't double-emitted. None for free async fns.
        """
        record_name, recv_expr = receiver if receiver is not None else (None, None)
        parts: list[str] = []
        if recv_expr is not None:
            parts.append(recv_expr)
        for cparam in self._classify_params(func, record_name):
            if cparam.cpp_name == "__self":
                continue
            if cparam.kind is _CoroParamKind.STATIC_PROTOCOL:
                parts.append(f"std::move({cparam.cpp_name})")
            else:
                parts.append(cparam.cpp_name)
        return ", ".join(parts)

    # -- poll() body ----------------------------------------------------------

    @contextlib.contextmanager
    def _resumable_frame_ctx(self, func: TpyFunction, record_name: str | None):
        """Set up + tear down resumable-frame ctx state for an async body.

        Routes through `StatementGenerator.setup_body_scope` so async bodies
        get the same per-scope state setup as sync (reassigned_vars,
        aliased_vars, movable_locals, etc. populated from sema scan).
        Layers the resumable-frame fields (`generator_field_names`,
        `generator_self_ref`, etc.) plus `in_generator_body=True` on top.
        """
        old_in_gen = self.ctx.in_generator_body
        old_field_names = self.ctx.generator_field_names
        old_optional_fields = self.ctx.generator_optional_fields
        old_frame_slot_locals = self.ctx.generator_frame_slot_locals
        old_for_info = self.ctx.generator_for_loop_info
        old_self_ref = self.ctx.generator_self_ref
        old_movable_locals = self.ctx.movable_locals

        # Reset frame-specific fields before setup_body_scope, since the
        # `setup_resumable_frame_locals` call inside it reads
        # `generator_for_loop_info` and writes to `generator_optional_fields`.
        self.ctx.generator_field_names = set()
        self.ctx.generator_optional_fields = set()
        self.ctx.generator_frame_slot_locals = set()
        self.ctx.generator_for_loop_info = {}

        local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
        for pname, ptype in func.params:
            local_ns.bind_variable(pname, ptype)

        crp, dcbp = self.functions.compute_body_const_sets(func, record_name)
        self.statements.setup_body_scope(
            func.params, func.return_type, func, local_ns,
            indent_level=1, is_method=bool(record_name),
            const_ref_params=crp, deep_const_borrow_params=dcbp,
        )

        self.ctx.in_generator_body = True
        # Belt-and-suspenders: `setup_body_scope` registers Own[T] params
        # as movable when `T.is_value_type()` is False, which already
        # covers most static-protocol shapes. Static-protocol frame
        # fields (e.g. `coro: Own[Awaitable[T]]` stored as the deduced
        # `T_coro`) are move-only by construction; force-register them
        # so the call-arg generator emits `std::move(coro)` rather than
        # a copy-into-temp + move, independent of how the protocol's
        # `is_value_type` resolves.
        for cparam in self._classify_params(func, record_name):
            if cparam.kind is _CoroParamKind.STATIC_PROTOCOL:
                self.ctx.movable_locals.add(cparam.cpp_name)
        if record_name:
            self.ctx.generator_self_ref = "__self"
            self.ctx.generator_field_names.add("__self")
        else:
            self.ctx.generator_self_ref = None
        for pname, _ in func.params:
            self.ctx.generator_field_names.add(pname)
        if func.generator_locals:
            for lname, _ltype in func.generator_locals:
                self.ctx.generator_field_names.add(lname)

        try:
            yield
        finally:
            self.ctx.in_generator_body = old_in_gen
            self.ctx.generator_field_names = old_field_names
            self.ctx.generator_optional_fields = old_optional_fields
            self.ctx.generator_frame_slot_locals = old_frame_slot_locals
            self.ctx.generator_for_loop_info = old_for_info
            self.ctx.generator_self_ref = old_self_ref
            self.ctx.movable_locals = old_movable_locals

    def gen_coro_finally_top_def(self, out: "TextIO", func: TpyFunction,
                                   record_name: str | None = None) -> None:
        """Emit member-function bodies for every `__finally_<n>()` helper
        the CFG produced (one per TryRegion with a finally body). No-op
        if the function has no finally bodies.
        """
        cfg = self._build_cfg(func)
        if not cfg.finally_helpers:
            return
        struct_name = self._struct_name_templated(func, record_name)

        with self._resumable_frame_ctx(func, record_name):
            for helper_name, body_stmts in cfg.finally_helpers:
                self._emit_template_header(out, func)
                out.write(f"void {struct_name}::{helper_name}() {{\n")
                self.ctx.indent_level = 1
                for stmt in body_stmts:
                    self.statements.gen_stmt(out, stmt)
                self.ctx.indent_level = 0
                out.write(f"}}\n")

    def gen_coro_poll_def(self, out: "TextIO", func: TpyFunction,
                            record_name: str | None = None) -> None:
        """Emit the `poll()` method body in the .cpp file (or inline-in-hpp
        for templates -- the caller handles placement). When `record_name`
        is given, the struct is `__coro_<Record>_<func>` and the body
        sees `self.X` as `__self.X` (parallels generator methods).
        """
        struct_name = self._struct_name_templated(func, record_name)
        cfg = self._build_cfg(func)
        has_yields = bool(cfg.yield_sites)

        self.ctx.emit_source_comment(out, func.loc)
        self._emit_template_header(out, func)
        out.write(f"{self._poll_ret_cpp(func)} {struct_name}::__poll__(::tpystd::coro::Waker waker) {{\n")
        if not has_yields:
            # No awaits: waker unused. Generators emit (void)waker for the
            # same reason; reuse the pattern.
            out.write(f"{INDENT}(void)waker;\n")

        # Async return-rewrite needs the C++ Poll<T> type and DONE
        # state label so `return v` lowers correctly inside the state
        # machine.
        old_in_async = getattr(self.ctx, "in_async_coro_body", False)
        old_async_ret_cpp = getattr(self.ctx, "async_coro_return_cpp", None)
        old_async_done_label = getattr(self.ctx, "async_coro_done_state", None)

        with self._resumable_frame_ctx(func, record_name):
            self.ctx.in_async_coro_body = True
            self.ctx.async_coro_return_cpp = self._ret_cpp(func)
            self.ctx.async_coro_done_state = "S_DONE"
            self.ctx.current_return_type = func.return_type
            try:
                self._emit_state_machine(out, func, cfg)
            finally:
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
        for_uid_map = self._prescan_async_for_loops(func, body)
        with_uid_map = self._prescan_with_stmts(func, body)
        try_finally_uid_map = self._prescan_async_try_finally(func, body)
        builder = rcfg.CFGBuilder(
            payload_factory=self._make_await_payload,
            for_uid_map=for_uid_map,
            with_uid_map=with_uid_map,
            try_finally_uid_map=try_finally_uid_map,
            func_returns_void=self._is_void_return(func),
        )
        try:
            cfg = builder.build_async(body)
        except rcfg._CFGNotYetSupported as e:
            raise CodeGenError(e.msg, loc=e.loc)
        # Stash the builder so callers (emit) can look up handler
        # entries via builder.get_handler_entry().
        func._async_cfg_builder = builder
        func._async_cfg = cfg
        return cfg

    def _prescan_async_for_loops(
            self, func: TpyFunction,
            body: list[TpyStmt]) -> dict[int, int]:
        """Find for-loops whose body contains await OR are `async for`
        (M6). For each, allocate a uid, register frame-field declarations
        and register the loop variable as a hoisted local so it persists
        across suspensions.

        Sync for-with-await (M3.1): two slots,
        `(__for_itr_N: decltype(::tpy::__iter__(it)),
          __for_r_N: decltype(itr.__next__()))`.

        Async-for (M6): one slot,
        `__for_itr_N: decltype(it.__aiter__())`. No `__for_r_N` slot --
        the advance is a Yield(await __anext__()) and the unwrapped
        value goes straight to the loop var via the standard resume-bind
        path. Also populates `func._async_for_struct_names[uid]` with
        the C++ name of the `__anext__` sub-coro struct so the Yield
        emit can size `__sub_<i>` and emplace it.

        Returns {id(TpyForEach) -> uid} for the CFG builder. Frame fields
        live on `func._async_for_fields` as `[(name, cpp_type)]` consumed
        by gen_coro_struct.
        """
        cached_map = getattr(func, "_async_for_uid_map", None)
        if cached_map is not None:
            return cached_map
        uid_map: dict[int, int] = {}
        fields_out: list[tuple[str, str]] = []
        struct_names_out: dict[int, str] = {}
        counter = [0]

        def walk(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                if isinstance(s, TpyForEach) and (
                        s.is_async or rcfg._stmts_have_any_await(s.body)):
                    cur_uid = counter[0]
                    counter[0] += 1
                    iter_t = self.types.get_resolved_type(s.iterable)
                    if iter_t is None:
                        raise CodeGenError(
                            "for-loop iterable has no resolved type "
                            "(async for-with-await pre-scan)",
                            loc=s.loc)
                    src_cpp = self.types.type_to_cpp(unwrap_ref_type(iter_t))
                    uid_map[id(s)] = cur_uid
                    if s.is_async:
                        iter_field_type = (
                            f"std::decay_t<decltype(std::declval<"
                            f"{src_cpp}&>().__aiter__())>")
                        fields_out.append(
                            (f"__for_itr_{cur_uid}", iter_field_type))
                        if s.async_aiter_type is None:
                            raise CodeGenError(
                                "async-for missing resolved aiter type "
                                "(internal: sema didn't populate "
                                "stmt.async_aiter_type)", loc=s.loc)
                        struct_names_out[cur_uid] = self._sub_struct_qualname(
                            s.async_aiter_type, "__anext__")
                    else:
                        iter_field_type = (
                            f"std::decay_t<decltype(::tpy::__iter__"
                            f"(std::declval<{src_cpp}&>()))>")
                        result_field_type = (
                            f"decltype(std::declval<{iter_field_type}&>()"
                            f".__next__())")
                        fields_out.append(
                            (f"__for_itr_{cur_uid}", iter_field_type))
                        fields_out.append(
                            (f"__for_r_{cur_uid}", result_field_type))
                    elem_t = (unwrap_ref_type(s.elem_type)
                              if s.elem_type else None)
                    if elem_t is None:
                        raise CodeGenError(
                            "async for-with-await: loop var has no "
                            "resolved elem_type", loc=s.loc)
                    if func.generator_locals is None:
                        func.generator_locals = []
                    if not any(n == s.var for n, _ in func.generator_locals):
                        func.generator_locals.append((s.var, elem_t))
                if hasattr(s, "sub_bodies"):
                    for b in s.sub_bodies():
                        walk(b)

        walk(body)
        func._async_for_uid_map = uid_map
        func._async_for_fields = fields_out
        func._async_for_struct_names = struct_names_out
        return uid_map

    def _sub_struct_qualname(
            self, owner: 'NominalType | None', method: str,
            inferred_type_args: 'tuple[TpyType, ...] | None' = None,
            *, module_qual: str | None = None,
            extra_template_args: 'list[str] | None' = None) -> str:
        """Build the C++ name of the sub-coro struct generated for a
        statically-resolved await (free function or method).

        Free function (owner=None, same module): `__coro_<name>[<inferred_args>]`.
        Free function (owner=None, cross-module via module_qual):
            `<callee_ns>::__coro_<name>[<inferred_args>]`.
        Method on non-generic class: `<ns>::__coro_<Record>_<name>
            [<inferred_args>]`.
        Method on generic class: `<ns>::__coro_<Record>_<name>
            <owner_type_args>`. (The class-generic + method-generic
            case is currently rejected at sema, so owner_type_args and
            inferred_type_args are not composed today.)

        `extra_template_args` appends concrete C++ types (typically
        `std::remove_cvref_t<decltype(arg)>`) for each static-protocol
        param on the callee -- these correspond to the `T_<pname>`
        template args declared on the callee's struct.
        """
        ns_qual = ""
        owner_name = None
        owner_args_suffix = ""
        if owner is not None:
            owner_cpp = self.types.type_to_cpp(owner)
            ns_prefix = owner_cpp.split("<", 1)[0]
            ns_qual = (ns_prefix.rsplit("::", 1)[0] + "::"
                       if "::" in ns_prefix else "")
            owner_name = owner.name
            if owner.type_args:
                inner_cpps = [self.types.type_to_cpp(ta)
                              for ta in owner.type_args]
                owner_args_suffix = "<" + ", ".join(inner_cpps) + ">"
        elif module_qual is not None:
            # Cross-module free-function await: qualify with the
            # callee module's C++ namespace.
            ns_qual = f"::{module_to_cpp_namespace(module_qual)}::"
        bare = AsyncCoroCodegen._sub_struct_name(method, owner_name)
        # Combined template-arg list: callee's explicit `[T1, ...]` from
        # the call's inferred substitution, followed by any
        # `T_<pname>` extras deduced from static-protocol args.
        all_args: list[str] = []
        if inferred_type_args and not (owner is not None and owner.type_args):
            all_args.extend(self.types.type_to_cpp(ta)
                             for ta in inferred_type_args)
        if extra_template_args:
            all_args.extend(extra_template_args)
        suffix = ("<" + ", ".join(all_args) + ">") if all_args else ""
        return f"{ns_qual}{bare}{owner_args_suffix}{suffix}"

    def _extra_template_args_for_await(self, call: TpyExpr) -> list[str]:
        """Compute the per-static-protocol `T_<pname>` template-arg
        spellings for the sub-coro struct of an inline await.

        Each callee static-protocol param gets one extra template arg
        on the struct (see `_protocol_template_parts`); at the call
        site that arg is the concrete type of the corresponding
        argument, spelled as `std::remove_cvref_t<decltype(<arg>)>` so
        the compiler deduces it without us having to spell it.
        Temps queued by gen_expr are discarded -- decltype doesn't
        evaluate, and the same arg's gen_expr will re-run at emplace
        time when the temps are actually needed.
        """
        if not isinstance(call, (TpyCall, TpyMethodCall)):
            return []
        fi = call.resolved_function_info
        if fi is None or not fi.params:
            return []
        if not any(
                self.functions.protocols.is_static_protocol_param(p.type)
                for p in fi.params):
            return []
        out: list[str] = []
        checkpoint = self.ctx.temps.checkpoint()
        for i, pinfo in enumerate(fi.params):
            if not self.functions.protocols.is_static_protocol_param(pinfo.type):
                continue
            if i >= len(call.args):
                # Defensive: callee param without a corresponding arg
                # at the call site (defaults aren't supported on async
                # static-protocol params today; bail rather than emit
                # a malformed template arg).
                self.ctx.temps.rollback_to(checkpoint)
                return []
            arg_cpp = self.expressions.gen_expr(call.args[i])
            out.append(f"std::remove_cvref_t<decltype({arg_cpp})>")
        self.ctx.temps.rollback_to(checkpoint)
        return out


    def _prescan_with_stmts(
            self, func: TpyFunction,
            body: list[TpyStmt]) -> dict[int, list[int]]:
        """Find `with` stmts whose body contains await. For each, allocate
        a ctx_n per WithItem, declare a `__with_ctx_<n>` frame field of
        the context-manager's C++ type, and register any `as` target
        names as hoisted locals.

        Returns {id(TpyWith) -> [ctx_n_per_item]}; frame fields land on
        `func._with_fields` as `[(name, cpp_type)]`.
        """
        cached_map = getattr(func, "_with_uid_map", None)
        if cached_map is not None:
            return cached_map
        uid_map: dict[int, list[int]] = {}
        fields_out: list[tuple[str, str]] = []
        counter = [0]

        # Async-with sub-coro struct names by ctx_n. Populated alongside
        # the per-ctx frame field. Emit uses this to size __sub_<i>
        # slots and to write the inline factory call.
        struct_names_out: dict[int, tuple[str, str]] = {}

        def walk(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                # Sync `with` only needs the frame slot when its body
                # contains an await (M3.2). `async with` always needs
                # one because `__aenter__` / `__aexit__` are themselves
                # the suspensions, regardless of whether the body has
                # any other await.
                if (isinstance(s, TpyWith)
                        and (s.is_async
                             or rcfg._stmts_have_any_await(s.body))):
                    per_item: list[int] = []
                    for item in s.items:
                        cur_n = counter[0]
                        counter[0] += 1
                        per_item.append(cur_n)
                        ctx_t = self.types.get_resolved_type(item.context_expr)
                        if ctx_t is None:
                            raise CodeGenError(
                                "with-stmt context manager has no resolved "
                                "type (async with-with-await pre-scan)",
                                loc=s.loc)
                        ctx_cpp = self.types.type_to_cpp(unwrap_ref_type(ctx_t))
                        fields_out.append((f"__with_ctx_{cur_n}", ctx_cpp))
                        # For async-with, compute the qualified C++ names
                        # of the __aenter__ / __aexit__ coro structs so
                        # struct-emit and _emit_suspend can look them up
                        # by ctx_n without needing the types helper at
                        # the CFG layer.
                        if s.is_async:
                            ctx_inner = unwrap_ref_type(ctx_t)
                            if not isinstance(ctx_inner, NominalType):
                                raise CodeGenError(
                                    "async-with context manager type is "
                                    "not a record (internal)",
                                    loc=s.loc)
                            # Build the aenter/aexit sub-coro struct
                            # names using the canonical struct namer
                            # (same shape as M4 async-method coro
                            # structs). The C++ namespace is derived
                            # from the CM's qualified type by
                            # stripping any template args first (so
                            # `::ns::Foo<T>` yields a `::ns::` prefix,
                            # not `::ns::Foo<T>::`). Type args are
                            # re-attached as a suffix on the coro
                            # struct reference -- the struct itself is
                            # templated over the same T as the CM.
                            # Generic CMs are rejected at sema today (see
                            # registration.py); the helper handles type_args
                            # so this prescan stays robust if that lifts.
                            struct_names_out[cur_n] = (
                                self._sub_struct_qualname(ctx_inner, "__aenter__"),
                                self._sub_struct_qualname(ctx_inner, "__aexit__"),
                            )
                        if item.target is not None:
                            enter_t = (unwrap_ref_type(item.enter_type)
                                       if item.enter_type else None)
                            if enter_t is None:
                                raise CodeGenError(
                                    "with-stmt target has no resolved "
                                    "enter_type", loc=s.loc)
                            if func.generator_locals is None:
                                func.generator_locals = []
                            if not any(n == item.target
                                       for n, _ in func.generator_locals):
                                func.generator_locals.append(
                                    (item.target, enter_t))
                            # For str-typed as-targets, the C++
                            # `__enter__()` / `__aenter__()` returns
                            # `std::string` by value but sema's `str`
                            # lowers to `std::string_view`. Tracking
                            # the name here promotes the frame field's
                            # storage type to `std::string` (owning)
                            # so the field doesn't alias a temporary
                            # that dies at the assignment's semicolon.
                            # Applies to both sync `with` and async
                            # `with`: the Poll<std::string>::value()
                            # extraction in async-with's resume case
                            # produces the same temporary shape as the
                            # sync __enter__() return.
                            resolved_enter_t = self.types.resolve_type(enter_t)
                            if is_str_category(resolved_enter_t):
                                if not hasattr(func, "_with_owning_str_targets"):
                                    func._with_owning_str_targets = set()
                                func._with_owning_str_targets.add(
                                    item.target)
                    uid_map[id(s)] = per_item
                if hasattr(s, "sub_bodies"):
                    for b in s.sub_bodies():
                        walk(b)

        walk(body)
        func._with_uid_map = uid_map
        func._with_fields = fields_out
        func._async_with_struct_names = struct_names_out
        return uid_map

    def _prescan_async_try_finally(
            self, func: TpyFunction,
            body: list[TpyStmt]) -> dict[int, int]:
        """Find try-stmts whose finally body contains an await. For
        each, allocate a uid and declare:
          * `__finally_exc_<n>` -- `std::exception_ptr` (always).
          * `__finally_pending_<n>` -- `bool` (only when try body or
             any handler body contains a reachable `return`).
          * `__finally_ret_<n>` -- function's return type, for the
             same reason and only when the async def is non-void.
        Returns {id(TpyTry) -> uid}; field declarations land on
        `func._async_try_finally_fields`."""
        cached_map = getattr(func, "_async_try_finally_uid_map", None)
        if cached_map is not None:
            return cached_map
        uid_map: dict[int, int] = {}
        fields_out: list[tuple[str, str]] = []
        counter = [0]
        is_void = self._is_void_return(func)
        ret_cpp = self._ret_cpp(func) if not is_void else None

        def walk(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                if (isinstance(s, TpyTry)
                        and s.finally_body
                        and rcfg._stmts_have_any_await(s.finally_body)):
                    cur_uid = counter[0]
                    counter[0] += 1
                    uid_map[id(s)] = cur_uid
                    fields_out.append(
                        (f"__finally_exc_{cur_uid}", "std::exception_ptr"))
                    has_return = (rcfg._stmts_have_any_return(s.try_body)
                                   or any(rcfg._stmts_have_any_return(h.body)
                                           for h in s.handlers))
                    if has_return:
                        fields_out.append(
                            (f"__finally_pending_{cur_uid}", "bool"))
                        if ret_cpp is not None:
                            fields_out.append(
                                (f"__finally_ret_{cur_uid}", ret_cpp))
                # `async with` desugars to a try/finally where the
                # finally body is `await __cm.__aexit__(...)`. The
                # synthetic finally needs the same set of frame slots
                # as a user-written try/finally-with-await; allocate
                # them in the same shared uid pool here.
                if isinstance(s, TpyWith) and s.is_async:
                    cur_uid = counter[0]
                    counter[0] += 1
                    uid_map[id(s)] = cur_uid
                    fields_out.append(
                        (f"__finally_exc_{cur_uid}", "std::exception_ptr"))
                    if rcfg._stmts_have_any_return(s.body):
                        fields_out.append(
                            (f"__finally_pending_{cur_uid}", "bool"))
                        if ret_cpp is not None:
                            fields_out.append(
                                (f"__finally_ret_{cur_uid}", ret_cpp))
                if hasattr(s, "sub_bodies"):
                    for b in s.sub_bodies():
                        walk(b)

        walk(body)
        func._async_try_finally_uid_map = uid_map
        func._async_try_finally_fields = fields_out
        return uid_map

    def _make_await_payload(self, await_node: TpyAwait, host_stmt: TpyStmt,
                             kind, bind_target, return_stmt) -> 'rcfg.AwaitPayload':
        """CFGBuilder payload factory: derives mode + sub_field_cpp_type
        from the await's sema annotations."""
        if await_node.awaited_async_func_name is not None:
            mode = rcfg.AwaitMode.INLINE
            inferred_type_args = getattr(
                await_node.value, "inferred_type_args", None)
            # Cross-module free-function await spelled `mod.func(...)`:
            # operand is a TpyMethodCall whose receiver is the module
            # (no class owner). Use the module qualifier to namespace
            # the sub-coro struct name.
            module_qual = None
            if (isinstance(await_node.value, TpyMethodCall)
                    and await_node.awaited_method_owner_type is None):
                module_qual = (await_node.value.user_module_call
                               or await_node.value.builtin_module_call)
            extra_template_args = self._extra_template_args_for_await(
                await_node.value)
            sub_cpp = self._sub_struct_qualname(
                await_node.awaited_method_owner_type,
                await_node.awaited_async_func_name,
                inferred_type_args,
                module_qual=module_qual,
                extra_template_args=extra_template_args)
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
            elif is_stable_address_lvalue(await_node.value):
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
            elif isinstance(t, rcfg.AsyncForAdvance):
                preds[t.has_value_bb].append(bid)
                preds[t.exhausted_bb].append(bid)
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
        # WithRegion suppression targets: any with whose __exit__ may
        # suppress an exception transitions state directly to
        # post_with_bb from inside the catch (not a normal CFG edge).
        # Mark every such post_with_bb as a case entry so the catch's
        # `__state = ...; continue;` lands on a labeled case.
        with_post_bbs: set[int] = set()
        for bb in cfg.blocks.values():
            for r in bb.region_stack:
                if (isinstance(r, rcfg.WithRegion)
                        and r.item.exit_can_suppress):
                    with_post_bbs.add(r.post_with_bb)
        for bid in with_post_bbs:
            if bid not in case_entries:
                case_entries[bid] = _StateLabel(_StateKind.JOIN, join_idx)
                join_idx += 1
        # CFG-based finally entry BBs: the TryRegion's catch-all
        # transitions state directly to `finally_entry_bb` from inside
        # the catch (not a normal CFG edge). The Fall on the normal
        # exit path region-crosses out of the TryRegion which already
        # makes this BB a case entry, but mark explicitly to cover the
        # "try body always raises" case (no Fall predecessor).
        finally_entry_bbs: set[int] = set()
        for bb in cfg.blocks.values():
            for r in bb.region_stack:
                if (isinstance(r, rcfg.TryRegion)
                        and r.finally_entry_bb is not None):
                    finally_entry_bbs.add(r.finally_entry_bb)
        for bid in finally_entry_bbs:
            if bid not in case_entries:
                case_entries[bid] = _StateLabel(_StateKind.JOIN, join_idx)
                join_idx += 1
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

    def _case_is_no_throw(self, cfg: 'rcfg.CFG', entry_bb: int) -> bool:
        """True iff the case body that starts at `entry_bb` provably
        cannot throw anything an in-scope handler would catch.
        Conservative: requires the BB to consist solely of a Yield
        terminator with literal / simple-name emplace args, with no
        user stmts and not a resume entry (resume cases emit a cancel
        check + sub poll that can both throw)."""
        bb = cfg.blocks[entry_bb]
        if bb.stmts:
            return False
        if cfg.resume_to_yield().get(entry_bb) is not None:
            return False
        t = bb.terminator
        if not isinstance(t, rcfg.Yield):
            return False
        return self._payload_args_no_throw(t.payload)

    def _payload_args_no_throw(self,
                                 payload: 'rcfg.AwaitPayload') -> bool:
        """True if emitting the Yield's emplace cannot throw."""
        # Async-with synthetic yields emplace from the CM frame slot
        # (`*__with_ctx_<n>`) plus monostate literals -- provably
        # no-throw and never touch the payload's `operand_expr`.
        if payload.async_with_kind is not None:
            return True
        if payload.mode is rcfg.AwaitMode.BORROWED:
            return self._expr_is_simple(payload.operand_expr)
        if payload.mode is rcfg.AwaitMode.ERASED:
            return self._expr_is_simple(payload.operand_expr)
        # INLINE: emplace args are the call's args.
        call = payload.operand_expr
        if not isinstance(call, TpyCall):
            return False
        return all(self._expr_is_simple(a) for a in call.args)

    @staticmethod
    def _expr_is_simple(expr: TpyExpr) -> bool:
        """True for literals, bare names, and shallow coercions of
        either. These can be emitted without invoking constructors /
        function calls that could throw."""
        if isinstance(expr, TpyCoerce):
            return AsyncCoroCodegen._expr_is_simple(expr.expr)
        return isinstance(expr, (TpyIntLiteral, TpyFloatLiteral,
                                   TpyStrLiteral, TpyBoolLiteral,
                                   TpyNoneLiteral, TpyName))

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
        # Pending-return ctx: when this case is inside the try
        # body or handler body of a CFG-based finally, install ctx
        # state so `return` inside the body routes through the
        # pending-return slot.
        prev_pending = self._snapshot_pending_return_ctx()
        pending = self._pending_return_info_for_region_stack(bb.region_stack)
        if pending is not None:
            flag, slot, finally_entry_bb, boundary = pending
            target_label = case_entries[finally_entry_bb].cpp_name()
            self.ctx.async_pending_return_flag = flag
            self.ctx.async_pending_return_slot = slot
            self.ctx.async_pending_return_target_state = target_label
            self.ctx.async_pending_return_boundary = boundary
        try:
            # Emit `try {` for each TryRegion / WithRegion. For each
            # TryRegion, collect the list of "inter-region finally
            # helpers" between THIS TryRegion and the next-inner
            # try-emitting region (TryRegion or WithRegion): each
            # ExceptRegion's parent_finally goes in the TryRegion's
            # catch because its source-level try frame is no longer on
            # the C++ try stack but is still logically active.
            # WithRegions inside the gap get their own C++ try and
            # don't contribute extras.
            tryctx_stack: list[tuple[rcfg.Region, tuple[str, ...]]] = []
            # Skip the try/catch wrap entirely for case bodies provably
            # no-throw (typical setup case: empty stmts + Yield with
            # literal/name emplace args). The matching live catch sits
            # on the resume case where the sub-future's poll runs and
            # CAN throw.
            no_throw = self._case_is_no_throw(cfg, entry_bb)
            try_emitting = ([] if no_throw else
                             [i for i, r in enumerate(bb.region_stack)
                              if isinstance(r, (rcfg.TryRegion,
                                                rcfg.WithRegion))])
            for j, i in enumerate(try_emitting):
                region = bb.region_stack[i]
                if isinstance(region, rcfg.TryRegion):
                    next_emit = (try_emitting[j + 1]
                                 if j + 1 < len(try_emitting)
                                 else len(bb.region_stack))
                    extras: list[str] = []
                    for k in range(i + 1, next_emit):
                        mid = bb.region_stack[k]
                        if isinstance(mid, rcfg.ExceptRegion):
                            if mid.parent_finally is not None:
                                extras.append(mid.parent_finally)
                    extras.reverse()
                else:
                    extras = []
                out.write(f"{body_indent}try {{\n")
                tryctx_stack.append((region, tuple(extras)))
                self.ctx.indent_level += 1
                body_indent = self.ctx.indent()

            # Emit the case body: resume step if this is a yield-resume,
            # then BB statements, then terminator.
            self._emit_case_body(out, cfg, entry_bb, case_entries, func)

            # Close regions innermost first.
            for region, extras in reversed(tryctx_stack):
                self.ctx.indent_level -= 1
                body_indent = self.ctx.indent()
                out.write(f"{body_indent}}}")
                if isinstance(region, rcfg.TryRegion):
                    self._emit_try_region_catches(
                        out, body_indent, region, cfg, case_entries,
                        entry_bb, func, extras)
                else:
                    self._emit_with_region_catches(
                        out, body_indent, region, case_entries)
                out.write("\n")
        finally:
            for _ in range(pushed_finally):
                self.ctx.finally_stack.pop()
            self._restore_pending_return_ctx(prev_pending)

    def _snapshot_pending_return_ctx(self) -> tuple:
        return (self.ctx.async_pending_return_flag,
                self.ctx.async_pending_return_slot,
                self.ctx.async_pending_return_target_state,
                self.ctx.async_pending_return_boundary)

    def _restore_pending_return_ctx(self, snap: tuple) -> None:
        (self.ctx.async_pending_return_flag,
         self.ctx.async_pending_return_slot,
         self.ctx.async_pending_return_target_state,
         self.ctx.async_pending_return_boundary) = snap

    def _finally_helpers_for_region_stack(
            self, region_stack: tuple) -> list:
        """Compute the finally-emit closures for BBs with this
        region_stack. Each TryRegion / ExceptRegion contributes its
        finally helper name (called via `this->name()`); each WithRegion
        contributes a closure that emits `(*__with_ctx_<n>).__exit__(
        {}, nullptr/{}, {})`. Order: outermost first (innermost ends up
        on top of the stack)."""
        helpers: list = []
        for region in region_stack:
            if isinstance(region, rcfg.TryRegion):
                if region.finally_helper_name is not None:
                    helpers.append(("helper", region.finally_helper_name))
            elif isinstance(region, rcfg.ExceptRegion):
                if region.parent_finally is not None:
                    helpers.append(("helper", region.parent_finally))
            elif isinstance(region, rcfg.WithRegion):
                helpers.append(("with", region))
        return helpers

    def _pending_return_info_for_region_stack(
            self, region_stack: tuple) -> 'tuple[str, str | None, int, int] | None':
        """If a CFG-based finally is active for this region_stack (i.e.
        a TryRegion with `pending_return_flag` OR an ExceptRegion whose
        parent had one), return `(flag, slot, finally_entry_bb,
        boundary)` -- boundary is the count of finally_stack frames
        contributed by regions OUTSIDE the CFG-based try. Returns None
        otherwise."""
        boundary = 0
        for region in region_stack:
            if isinstance(region, rcfg.TryRegion):
                if region.pending_return_flag is not None:
                    return (region.pending_return_flag,
                            region.pending_return_slot,
                            region.finally_entry_bb,
                            boundary)
                if region.finally_helper_name is not None:
                    boundary += 1
            elif isinstance(region, rcfg.ExceptRegion):
                if region.parent_pending_return_flag is not None:
                    return (region.parent_pending_return_flag,
                            region.parent_pending_return_slot,
                            region.parent_finally_entry_bb,
                            boundary)
                if region.parent_finally is not None:
                    boundary += 1
            elif isinstance(region, rcfg.WithRegion):
                boundary += 1
        return None

    def _push_finally_helpers(self, helpers: list) -> int:
        """Push FinallyContext entries for each helper. Returns the
        count pushed for matching pop in a `finally:` clause."""
        count = 0
        for kind, payload in helpers:
            if kind == "helper":
                helper_name = payload
                def _emit_finally(o: "TextIO", ind: str,
                                  n=helper_name) -> None:
                    o.write(f"{ind}this->{n}();\n")
            else:  # "with"
                region = payload
                def _emit_finally(o: "TextIO", ind: str,
                                  r=region) -> None:
                    self._emit_with_exit(o, ind, r, on_exception=False)
            fctx = FinallyContext(
                emit_finally=_emit_finally, terminates=False, loop_depth=0)
            self.ctx.finally_stack.append(fctx)
            count += 1
        return count

    def _emit_with_region_catches(self, out: "TextIO", indent: str,
                                    region: 'rcfg.WithRegion',
                                    case_entries: dict[int, _StateLabel]
                                    ) -> None:
        """Emit `} catch (...) { __exit__(...); throw; }` for a
        WithRegion at the close of a case body. When
        `item.exit_can_suppress` is True, a `catch (BaseException&)`
        clause first calls __exit__ with the exception and, on a True
        return, transitions to the WithRegion's post_with_bb state
        (suppressing the exception). The foreign catch-all always
        cleanup-calls __exit__ with `nullptr` exc and rethrows."""
        item = region.item
        n = region.ctx_n
        can_suppress = item.exit_can_suppress
        takes_exc_val = item.exit_takes_exc_val
        emit_tpy_catch = can_suppress or takes_exc_val
        # post_with_bb is only marked a case entry when this WithRegion
        # may suppress; without suppression the catch unconditionally
        # rethrows and never needs the label.
        post_label = (case_entries[region.post_with_bb].cpp_name()
                      if can_suppress else None)
        if emit_tpy_catch:
            out.write(f" catch (::tpy::BaseException& __exc_{n}) {{\n")
            self.ctx.indent_level += 1
            catch_ind = self.ctx.indent()
            if can_suppress:
                out.write(
                    f"{catch_ind}if (!(*__with_ctx_{n}).__exit__("
                    f"{{}}, &__exc_{n}, {{}})) throw;\n")
                out.write(f"{catch_ind}__state = {post_label};\n")
                out.write(f"{catch_ind}continue;\n")
            else:
                self._emit_with_exit(out, catch_ind, region,
                                            on_exception=True)
                out.write(f"{catch_ind}throw;\n")
            self.ctx.indent_level -= 1
            out.write(f"{indent}}} catch (...) {{\n")
        else:
            out.write(f" catch (...) {{\n")
        self.ctx.indent_level += 1
        catch_ind = self.ctx.indent()
        # Foreign exception (or non-suppressing path): cleanup-only call,
        # then rethrow. Don't pass &exc since the C++ side hasn't bound
        # one in this catch arm.
        out.write(f"{catch_ind}(*__with_ctx_{n}).__exit__({{}}, "
                  f"{'nullptr' if takes_exc_val else '{}'}, {{}});\n")
        out.write(f"{catch_ind}throw;\n")
        self.ctx.indent_level -= 1
        out.write(f"{indent}}}")

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
            # finallies) before propagating to the outer try. For
            # CFG-based finally, the inner catch saves
            # `std::current_exception()` to the parent's captured-exc
            # slot and transitions state to its finally entry instead
            # of plain rethrow -- mirrors the outer catch-all path.
            cfg_finally = region.captured_exc_field is not None
            has_throw_unwind = bool(handler_throw_finallies) or cfg_finally
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
            handler_stack_helpers: list = []
            if handler_entry is not None:
                handler_bb = cfg.blocks[handler_entry]
                handler_stack_helpers = self._finally_helpers_for_region_stack(
                    handler_bb.region_stack)
            self.ctx.finally_stack = []
            self._push_finally_helpers(handler_stack_helpers)
            old_except_tier = self.ctx.in_except_tier
            self.ctx.in_except_tier = "throw"
            # Pending-return ctx for the handler body: a return inside
            # the handler routes through the parent CFG-based finally's
            # pending-return slot.
            prev_pending = self._snapshot_pending_return_ctx()
            if handler_entry is not None:
                pending = self._pending_return_info_for_region_stack(
                    cfg.blocks[handler_entry].region_stack)
                if pending is not None:
                    flag, slot, finally_entry_bb, boundary = pending
                    target_label = case_entries[finally_entry_bb].cpp_name()
                    self.ctx.async_pending_return_flag = flag
                    self.ctx.async_pending_return_slot = slot
                    self.ctx.async_pending_return_target_state = target_label
                    self.ctx.async_pending_return_boundary = boundary
            try:
                if handler_entry is not None:
                    self._walk_inline(out, cfg, handler_entry,
                                       case_entries, func)
            finally:
                self.ctx.in_except_tier = old_except_tier
                self.ctx.finally_stack = old_finally_stack
                self._restore_pending_return_ctx(prev_pending)
            if has_throw_unwind:
                self.ctx.indent_level -= 1
                inner_close = self.ctx.indent()
                out.write(f"{inner_close}}} catch (...) {{\n")
                self.ctx.indent_level += 1
                inner_catch = self.ctx.indent()
                for helper in handler_throw_finallies:
                    out.write(f"{inner_catch}this->{helper}();\n")
                if cfg_finally:
                    assert region.finally_entry_bb is not None
                    fe_label = case_entries[region.finally_entry_bb].cpp_name()
                    out.write(f"{inner_catch}this->{region.captured_exc_field}"
                              f" = std::current_exception();\n")
                    out.write(f"{inner_catch}__state = {fe_label};\n")
                    out.write(f"{inner_catch}continue;\n")
                else:
                    out.write(f"{inner_catch}throw;\n")
                self.ctx.indent_level -= 1
                out.write(f"{inner_close}}}\n")
            self.ctx.indent_level -= 1
            out.write(f"{indent}}}")
        # Catch-all: reset sub, run inter-region finallies (innermost
        # first). For helper-based finally: call helper, re-throw. For
        # CFG-based finally: save current exception to the
        # captured-exc field and transition state to the finally entry
        # so the finally body runs in the state machine (with possible
        # suspensions); the saved exception is rethrown by the
        # AsyncFinallyExit stmt at the finally tail.
        out.write(" catch (...) {\n")
        self.ctx.indent_level += 1
        catch_indent = self.ctx.indent()
        if yield_for_case is not None:
            self._emit_sub_reset(out, catch_indent,
                                  yield_for_case.payload,
                                  yield_for_case.suspension_index)
        for helper in extra_finallies:
            out.write(f"{catch_indent}this->{helper}();\n")
        if region.captured_exc_field is not None:
            assert region.finally_entry_bb is not None
            label = case_entries[region.finally_entry_bb].cpp_name()
            out.write(f"{catch_indent}this->{region.captured_exc_field} "
                      f"= std::current_exception();\n")
            out.write(f"{catch_indent}__state = {label};\n")
            out.write(f"{catch_indent}continue;\n")
        else:
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
            elif isinstance(region, rcfg.WithRegion):
                # Leaving a with-region normally: __exit__(None, None, None).
                self._emit_with_exit(out, indent, region,
                                            on_exception=False)

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
                if isinstance(stmt, rcfg.AsyncForIterSetup):
                    self._emit_async_for_iter_setup(out, body_indent, stmt)
                elif isinstance(stmt, rcfg.WithEnter):
                    self._emit_with_enter(out, body_indent, stmt)
                elif isinstance(stmt, rcfg.AsyncWithSetup):
                    self._emit_async_with_setup(out, body_indent, stmt)
                elif isinstance(stmt, rcfg.AsyncFinallyExit):
                    self._emit_async_finally_exit(out, body_indent, stmt)
                else:
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
                self.ctx.temps.flush(out, body_indent)
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
            if isinstance(t, rcfg.AsyncForAdvance):
                self._emit_async_for_advance(
                    out, body_indent, cfg, t, case_entries, func, from_bb=cur)
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

    def _emit_async_finally_exit(self, out: "TextIO", indent: str,
                                   stmt: 'rcfg.AsyncFinallyExit') -> None:
        """Emit the saved-exception rethrow check + deferred-return
        check at the tail of a CFG-decomposed finally body. Both
        fields are cleared before extraction so a re-entry to the
        same try (e.g. in a loop) starts with no carried-over state."""
        f = stmt.captured_exc_field
        out.write(f"{indent}if (this->{f}) {{\n")
        inner = indent + INDENT
        out.write(f"{inner}std::exception_ptr __tmp = this->{f};\n")
        out.write(f"{inner}this->{f} = nullptr;\n")
        out.write(f"{inner}std::rethrow_exception(__tmp);\n")
        out.write(f"{indent}}}\n")
        if stmt.pending_return_flag is None:
            return
        # Deferred-return path. The flag is cleared first so control
        # unwinding to an outer CFG-based finally would not re-fire the
        # return -- though such nesting is currently rejected, this
        # keeps the slot semantically clean.
        flag = stmt.pending_return_flag
        out.write(f"{indent}if (this->{flag}) {{\n")
        out.write(f"{inner}this->{flag} = false;\n")
        # Walk any outer helpers (e.g. an enclosing helper-based
        # finally outside this CFG-based one) before emitting the
        # actual return. `_emit_finally_chain` handles its own
        # indent-level save/sync from the `inner` string.
        self.statements._emit_finally_chain(out, inner)
        done_state = self.ctx.async_coro_done_state or "S_DONE"
        out.write(f"{inner}__state = {done_state};\n")
        slot = stmt.pending_return_slot
        ret_cpp = self.ctx.async_coro_return_cpp
        if slot is None or ret_cpp == "void":
            out.write(f"{inner}{POLL_VOID_READY_RETURN}\n")
        else:
            out.write(f"{inner}return ::tpystd::tpy::Poll<{ret_cpp}>::ready"
                      f"(std::move(this->{slot}));\n")
        out.write(f"{indent}}}\n")

    def _emit_with_enter(self, out: "TextIO", indent: str,
                                stmt: 'rcfg.WithEnter') -> None:
        """Emit the with-stmt setup sequence:
            __with_ctx_<n> = <context_expr>;
            <target> = (*__with_ctx_<n>).__enter__();   # if target
            (*__with_ctx_<n>).__enter__();              # else
        """
        ctx_n = stmt.ctx_n
        item = stmt.item
        ctx_expr = self.expressions.gen_expr(item.context_expr)
        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}__with_ctx_{ctx_n} = {ctx_expr};\n")
        if item.target is not None:
            target = escape_cpp_name(item.target)
            out.write(f"{indent}{target} = (*__with_ctx_{ctx_n}).__enter__();\n")
        else:
            out.write(f"{indent}(*__with_ctx_{ctx_n}).__enter__();\n")

    def _emit_async_with_setup(self, out: "TextIO", indent: str,
                                 stmt: 'rcfg.AsyncWithSetup') -> None:
        """Emit the async-with frame-slot population:
            __with_ctx_<n> = <context_expr>;
        (Assignment into a `std::optional<CM>` frame field; constructs
        in place via `operator=`.) Subsequent Yield BBs (aenter/aexit)
        emplace `__sub_<i>` with `(*__with_ctx_<n>, ...)`."""
        ctx_n = stmt.ctx_n
        item = stmt.item
        ctx_expr = self.expressions.gen_expr(item.context_expr)
        self.ctx.temps.flush(out, indent)
        out.write(f"{indent}__with_ctx_{ctx_n} = {ctx_expr};\n")

    def _emit_with_exit(self, out: "TextIO", indent: str,
                               region: 'rcfg.WithRegion',
                               on_exception: bool) -> None:
        """Emit a single `__exit__` call. `on_exception=True` passes the
        catch-bound `__exc_<n>` (or `nullptr` when sema marked
        exit_takes_exc_val=False); otherwise passes the "normal exit"
        args (all empty / nullptr)."""
        item = region.item
        n = region.ctx_n
        if on_exception and item.exit_takes_exc_val:
            exc_arg = f"&__exc_{n}"
        elif item.exit_takes_exc_val:
            exc_arg = "nullptr"
        else:
            exc_arg = "{}"
        out.write(f"{indent}(*__with_ctx_{n}).__exit__("
                  f"{{}}, {exc_arg}, {{}});\n")

    def _emit_async_for_iter_setup(self, out: "TextIO", indent: str,
                                    stmt: 'rcfg.AsyncForIterSetup') -> None:
        """Initialize the for-loop iterator into its frame slot.
        Sync (M3.1):   __for_itr_<uid> = ::tpy::__iter__(<iterable>);
        Async (M6):    __for_itr_<uid> = (<iterable>).__aiter__();
        """
        iter_cpp = self.expressions.gen_expr(stmt.iterable_expr)
        self.ctx.temps.flush(out, indent)
        if stmt.is_async:
            out.write(f"{indent}__for_itr_{stmt.uid} = "
                      f"({iter_cpp}).__aiter__();\n")
        else:
            out.write(f"{indent}__for_itr_{stmt.uid} = ::tpy::__iter__({iter_cpp});\n")

    def _emit_async_for_advance(self, out: "TextIO", indent: str,
                                  cfg: 'rcfg.CFG',
                                  t: 'rcfg.AsyncForAdvance',
                                  case_entries: dict[int, _StateLabel],
                                  func: TpyFunction,
                                  from_bb: int) -> None:
        """Emit the next()/has-value/bind sequence for an
        AsyncForAdvance terminator, then transfer control to the body
        (`has_value_bb`) or loop exit (`exhausted_bb`).

        Both targets are case entries (cond_bb has 2 successors and the
        body BB is the loop-back target, so it's always multi-pred via
        the body's Fall back to cond_bb -> body via has_value)."""
        uid = t.uid
        stmt = t.stmt
        # exhausted path -- transition to exit state.
        out.write(f"{indent}__for_r_{uid} = "
                  f"(*__for_itr_{uid}).__next__();\n")
        out.write(f"{indent}if (!(*__for_r_{uid}).has_value()) {{\n")
        self.ctx.indent_level += 1
        inner = self.ctx.indent()
        self._emit_exit_region_finallies(
            out, inner,
            cfg.blocks[from_bb].region_stack,
            cfg.blocks[t.exhausted_bb].region_stack)
        if t.exhausted_bb in case_entries:
            out.write(f"{inner}__state = "
                      f"{case_entries[t.exhausted_bb].cpp_name()};\n")
            out.write(f"{inner}continue;\n")
        else:
            # Exit BB isn't a case entry (shouldn't happen given
            # _compute_case_entries counts AsyncForAdvance successors,
            # but stay defensive: walk inline).
            self._walk_inline(out, cfg, t.exhausted_bb, case_entries, func)
        self.ctx.indent_level -= 1
        out.write(f"{indent}}}\n")
        # has_value path -- bind loop var then continue to body.
        cpp_var = escape_cpp_name(stmt.var)
        out.write(f"{indent}{cpp_var} = ::tpy::unwrap_ref(*(*__for_r_{uid}));\n")
        self._walk_inline_or_jump(
            out, cfg, t.has_value_bb, case_entries, func, from_bb=from_bb)

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

    def _gen_coro_emplace_arg(self, arg: 'TpyExpr', arg_index: int,
                                call: 'TpyCall | TpyMethodCall') -> str:
        """Generate one arg for `__sub_N.emplace(...)` constructing a
        sub-coroutine. Mirrors the param-type-driven coercions sync call
        codegen applies in `_gen_call`: pointer-form `Optional[NonValue]`
        lift (P -> &P) when the callee param is the `T*` shape, plus
        the full `Own[T]` move-out machinery (`std::move(name)` for
        last-use movable lvalues bound to `Own[T]` params) via
        `gen_call_arg`. Without the latter, an async-def calling a
        callee with an `Own[T]` param (e.g. `await wait_for(coro, ...)`
        with `coro: Own[Awaitable[T]]`) would emit a bare name and the
        forwarding-ref ctor param would refuse to bind to the lvalue.
        """
        fi = call.resolved_function_info
        if fi is None or arg_index >= len(fi.params):
            return self.expressions.gen_expr(arg)
        ptype = fi.params[arg_index].type
        opt_arg = self.expressions._gen_optional_ptr_arg(arg, ptype)
        if opt_arg is not None:
            return opt_arg
        return self.expressions.gen_call_arg(arg, ptype)

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
        # `::tpy::poll_with_cancel` propagates the outer's cancel into
        # the in-flight sub before polling (so the sub observes the
        # cancel at its own suspension point and can run
        # `finally`-with-await cleanup) and throws CancelledError if
        # the sub races past the cancel. See runtime/cpp/include/tpy/
        # async.hpp for the full semantics.
        out.write(f"{indent}auto __r{suspension_index} = "
                  f"::tpy::poll_with_cancel({sub}, __cancel_pending, "
                  f"waker);\n")
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
            # When a CFG-based finally is active, route the
            # `return await X` through the pending-return slot + flag
            # and transition to the finally entry instead of emitting
            # Poll::ready directly; AsyncFinallyExit at the finally
            # tail will emit the deferred Poll::ready after the
            # rethrow check.
            pending_flag = self.ctx.async_pending_return_flag
            if pending_flag is not None:
                pending_slot = self.ctx.async_pending_return_slot
                target_state = self.ctx.async_pending_return_target_state
                boundary = self.ctx.async_pending_return_boundary
                assert target_state is not None
                if pending_slot is not None and not self._is_void_return(func):
                    out.write(f"{indent}this->{pending_slot} = "
                              f"std::move(__ret{suspension_index});\n")
                else:
                    out.write(f"{indent}(void)__ret{suspension_index};\n")
                out.write(f"{indent}this->{pending_flag} = true;\n")
                terminated = self.statements._emit_finally_chain(
                    out, indent, stop_at=boundary)
                if not terminated:
                    out.write(f"{indent}__state = {target_state};\n")
                    out.write(f"{indent}continue;\n")
                return
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
            if payload.async_with_kind is not None:
                # Async-with synthetic yield. Receiver is the CM frame
                # slot; args differ by kind.
                ctx_n = payload.async_with_ctx_n
                recv = f"(*__with_ctx_{ctx_n})"
                if payload.async_with_kind is rcfg.AsyncWithKind.AENTER:
                    out.write(f"{indent}{sub}.emplace({recv});\n")
                else:  # AEXIT -- cleanup-only call with all-None args
                    out.write(f"{indent}{sub}.emplace({recv}, "
                              f"::std::monostate{{}}, "
                              f"::std::monostate{{}}, "
                              f"::std::monostate{{}});\n")
            elif payload.async_for_uid is not None:
                uid = payload.async_for_uid
                out.write(f"{indent}{sub}.emplace(*__for_itr_{uid});\n")
            else:
                call = payload.operand_expr
                if isinstance(call, TpyCall):
                    args = [self._gen_coro_emplace_arg(arg, i, call)
                            for i, arg in enumerate(call.args)]
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}{sub}.emplace({', '.join(args)});\n")
                elif isinstance(call, TpyMethodCall):
                    is_module_call = (call.user_module_call is not None
                                      or call.builtin_module_call is not None)
                    if is_module_call:
                        # `module.func(...)` -- receiver is a namespace,
                        # not a value, so the sub-coro ctor takes only
                        # the function args.
                        args = [self._gen_coro_emplace_arg(a, i, call)
                                for i, a in enumerate(call.args)]
                        self.ctx.temps.flush(out, indent)
                        out.write(f"{indent}{sub}.emplace({', '.join(args)});\n")
                    else:
                        # Bound async method: prepend receiver as __self ctor arg.
                        recv_cpp = self.expressions.gen_expr(call.obj)
                        arg_cpps = [self._gen_coro_emplace_arg(a, i, call)
                                    for i, a in enumerate(call.args)]
                        self.ctx.temps.flush(out, indent)
                        joined = ", ".join([recv_cpp] + arg_cpps)
                        out.write(f"{indent}{sub}.emplace({joined});\n")
                else:
                    raise CodeGenError(
                        "internal: inline-mode await operand is not a call",
                        loc=None)
        elif payload.mode is rcfg.AwaitMode.ERASED:
            operand_cpp = self.expressions.gen_expr(payload.operand_expr)
            self.ctx.temps.flush(out, indent)
            out.write(f"{indent}{sub}.emplace(std::move({operand_cpp}));\n")
        elif payload.mode is rcfg.AwaitMode.BORROWED:
            operand_cpp = self.expressions.gen_expr(payload.operand_expr)
            self.ctx.temps.flush(out, indent)
            out.write(f"{indent}{sub} = &({operand_cpp});\n")
        else:
            raise CodeGenError(f"unknown await mode {payload.mode!r}",
                               loc=None)
        out.write(f"{indent}__state = "
                  f"{_StateLabel(_StateKind.RESUME, suspension_index).cpp_name()};\n")
