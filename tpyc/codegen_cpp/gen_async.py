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
import io
from dataclasses import dataclass, fields, is_dataclass
from enum import IntEnum
from functools import partial
from typing import NoReturn, TYPE_CHECKING

from ..identity_map import IdentityMap
from ..namespace import Namespace
from ..parse.nodes import (
    TpyFunction, TpyAwait, TpyStmt, TpyAssign, TpyVarDecl, TpyReturn,
    TpyExprStmt, TpyName, TpyExpr, TpyTry, TpyExceptHandler, TpyCall,
    TpyMethodCall, TpyFieldAccess, TpyForEach, TpyWhile, TpyWith, TpyWithItem,
    TpyTupleUnpack, TpyNestedDef, TpySubscript,
    TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBoolLiteral,
    TpyNoneLiteral, TpyCoerce,
    TpyArrayLiteral, TpyDictLiteral, TpySetLiteral, TpyListRepeat,
    TpyListComprehension, TpyDictComprehension, TpySetComprehension,
    is_stable_address_lvalue, walrus_bindings,
)


def collect_frame_nested_defs(stmts: 'list[TpyStmt]') -> 'list[TpyNestedDef]':
    """Nested defs anywhere in a resumable body, in source order. Each is
    emitted as a member function of the frame struct (locals are frame
    fields a lambda cannot capture, and a member is callable from every
    resume case). Nested-in-nested defs are rejected by sema."""
    out: 'list[TpyNestedDef]' = []
    for s in stmts:
        if isinstance(s, TpyNestedDef):
            out.append(s)
        else:
            for body in s.sub_bodies():
                out.extend(collect_frame_nested_defs(body))
    return out

# Collection literals / comprehensions have no concrete C++ type at a
# protocol-typed call site (the param is a concept) and no frame storage to
# survive a suspension, so they can't back an inline-await sub-future field.
_FRESH_COLLECTION_NODES = (
    TpyArrayLiteral, TpyDictLiteral, TpySetLiteral, TpyListRepeat,
    TpyListComprehension, TpyDictComprehension, TpySetComprehension,
)
from ..typesys import IntLiteralType, NominalType, OptionalType, OwnType, ReadonlyType, TupleType, TypeParamRef, unwrap_readonly, unwrap_ref_type, unwrap_own, unwrap_send_sync, VoidType, is_fn_type, is_dyn_protocol
from ..value_category import (async_return_form, AsyncReturnForm,
                              borrowing_frame_callee,
                              materializing_temp_source, peel_coerce)
from .gen_generators import (GeneratorCodegen, GeneratorForInfo,
                             owned_view_frame_params)
from ..type_def_registry import (is_str_type, is_str_category, is_big_int_type,
                                  is_bytes_category, is_owned_in_coro_frame,
                                  is_str_view_type, is_bytes_view_type,
                                  is_free_copy_scalar, view_owned_copy_init)
from . import emit_prims
from .context import INDENT, escape_cpp_name, CodeGenError, FinallyContext, qualified_cpp_name
from .protocols import protocol_param_template_name, fn_param_template_name
from .functions import default_to_cpp, default_emittable
from . import resumable_cfg as rcfg


def _stmts_have_return(stmts: "list[TpyStmt]") -> bool:
    """Recursively check whether any TpyReturn appears in a statement list."""
    for stmt in stmts:
        if isinstance(stmt, TpyReturn):
            return True
        if hasattr(stmt, "sub_bodies"):
            for body in stmt.sub_bodies():
                if _stmts_have_return(body):
                    return True
    return False


def _regions_have_pending_cleanup(regions: tuple) -> bool:
    """Return True if any region in the tuple will emit cleanup code.

    Used by _emit_exit_region_finallies to decide whether to defer the
    __finally_stop check: if the destination region stack still has pending
    cleanup (with.__exit__ or finally helpers), the stop-check must wait
    until those later states have run their cleanup."""
    for r in regions:
        if isinstance(r, rcfg.WithRegion):
            return True
        if isinstance(r, rcfg.FinallyRegion):
            # BBs inside a CFG-based finally body: the finally body itself
            # is the pending cleanup; don't stop before it runs.
            return True
        if isinstance(r, rcfg.TryRegion) and r.finally_helper_name is not None:
            return True
        if isinstance(r, rcfg.ExceptRegion) and r.parent_finally is not None:
            return True
    return False


if TYPE_CHECKING:
    from collections.abc import Callable
    from io import TextIO
    from ..typesys import TpyType
    from .context import CodeGenContext
    from .types import TypeMapper
    from .functions import FunctionGenerator

    # Saved (narrowed_vars, protocol_narrowings, literal_facts) snapshots that
    # _restore_resume_narrowings reverts after a narrowed scope is emitted.
    _NarrowingToken = tuple[dict[str, str | None],
                            dict[str, TpyType], dict[str, TpyType]]


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
    OWNED_VALUE: `Own[T]` non-value, non-static-protocol type (e.g.
        `Own[Cancellable[T]]` -- a @dynamic protocol, so the C++ shape is
        `unique_ptr<P>`, not a deduced template arg). Field stores by
        value; ctor takes `T&&` and moves in; the factory forwards via
        `std::move(name)`. Parallels STATIC_PROTOCOL minus the
        template-arg dance.
    FN: `Fn[[...], R]` callable param. Concrete callable type is deduced
        as an extra template arg `F_<pname>` (declared bare in the coro
        template header); field stores it by value, ctor takes
        `F_<pname>&&` and forwards. Identical mechanics to STATIC_PROTOCOL,
        only the template-header constraint differs (a plain `typename`
        vs a concept) -- mirrors the non-generator Fn handling in
        `functions.py`.
    OWNED_COPY: a `str` / `bytes` param (is_owned_in_coro_frame). The field is
        the owned storage type (`std::string` / `tpy::bytes`), but the
        factory/ctor take the borrow form (`std::string_view` / `BytesView`) --
        the form the call site passes -- and the init copies it into the owned
        field at frame construction (`std::string(...)` / `Bytes(...)`, the
        per-type `owned_copy_conv`). This OWNS the value, so it can't dangle
        across a suspension when the arg is a temporary; the cost is one copy
        at capture. `_param_borrows` excludes it (no caller storage held), and
        sema's `generator_borrow_param_indices` excludes the same set so the
        borrow-vs-own decision stays in one place. Explicit view types
        (`StrView`, `BytesView`, `Span[T]`) have no owned counterpart and stay
        borrow-form -- see BUGS.md.
    """
    REF = 0
    VALUE = 1
    POINTER = 2
    TYPE_PARAM = 3
    STATIC_PROTOCOL = 4
    OWNED_VALUE = 5
    FN = 6
    OWNED_COPY = 7


@dataclass(frozen=True)
class _CoroParam:
    cpp_name: str
    field_type: str  # type spelling for the frame-field declaration
    ctor_param_type: str  # type spelling for the constructor's parameter
    kind: _CoroParamKind
    # OWNED_COPY only: the ctor-init RHS expression copying the borrow param into
    # the owned field (`::tpy::Bytes(b_)`, or the optional-aware form for
    # `str|None` / `bytes|None`). Built via view_owned_copy_init.
    owned_copy_init: str = ""

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
        if self.kind in (_CoroParamKind.STATIC_PROTOCOL, _CoroParamKind.FN):
            return f"{self.ctor_param_type}&& {self.cpp_name}"
        if self.kind is _CoroParamKind.OWNED_VALUE:
            return f"{self.ctor_param_type} {self.cpp_name}"
        # OWNED_COPY: factory takes the borrow form by value (bare name), same
        # as VALUE; the ctor copies it into the owned field.
        return f"{self.ctor_param_type} {self.cpp_name}"

    def ctor_param_decl(self) -> str:
        if self.kind is _CoroParamKind.REF:
            return f"{self.ctor_param_type}& {self.cpp_name}"
        if self.kind in (_CoroParamKind.STATIC_PROTOCOL, _CoroParamKind.FN):
            return f"{self.ctor_param_type}&& {self.cpp_name}_"
        if self.kind is _CoroParamKind.OWNED_VALUE:
            return f"{self.ctor_param_type}&& {self.cpp_name}_"
        # VALUE / POINTER / TYPE_PARAM / OWNED_COPY: `_` suffix disambiguates
        # from the field name in the init list.
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
        if self.kind in (_CoroParamKind.STATIC_PROTOCOL, _CoroParamKind.FN):
            # `T_<pname>` / `F_<pname>` is the class template param, so the
            # ctor's `&&` reference-collapses (a plain lvalue ref when the
            # factory deduced a borrowed lvalue). Forward to bind both the
            # value and lvalue-ref cases; a bare std::move breaks the latter.
            return (f"{self.cpp_name}("
                    f"std::forward<{self.ctor_param_type}>({self.cpp_name}_))")
        if self.kind is _CoroParamKind.OWNED_COPY:
            # Copy the borrow-form param into the owned field at construction,
            # so the value survives suspensions without aliasing the caller
            # (std::string(view) for str, ::tpy::Bytes(view) for bytes; the
            # nullable forms copy the inner only when present).
            return f"{self.cpp_name}({self.owned_copy_init})"
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
            # Resume point after suspension `idx`. Shape-neutral name: the
            # same emitter serves both `await` and `yield` suspensions.
            return f"S_RESUME_{self.idx}"
        return f"S_JOIN_{self.idx}"


# Single C++ spelling of `Ready(unit)` for void-returning async defs.
# `async def -> None` lowers Poll[None]'s type-arg slot to std::monostate
# (the value-bearing-None unit type); the `ready` factory needs an actual
# value of that type, so we construct `std::monostate{}` explicitly.
# Centralised here so the emit sites in this module can't drift apart.
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
        functions: "FunctionGenerator",
    ):
        self.ctx = ctx
        self.types = types
        self.functions = functions
        # The body-context seeding (`setup_resumable_frame_locals`) consumes
        # the frame-layout plan but runs on the ctx, which has no emitter
        # back-reference -- hand it the builder.
        ctx.frame_layout_builder = self._frame_layout
        # Set by CodeGenerator after init; the resumable for-loop emit reuses
        # the legacy strategy analysis (`_analyze_for_strategy`).
        self.gen_generators: GeneratorCodegen
        # Resumable-shape discriminator read by the `_resumable_*` policy
        # seams. ASYNC (await -> __poll__ -> Poll<T>) is the default; the
        # GENERATOR shape (yield -> __next__ -> expected<T, StopIteration>)
        # is selected transiently via `_resumable_shape`.
        self._shape: rcfg.ResumableShape = rcfg.ResumableShape.ASYNC

    @contextlib.contextmanager
    def _resumable_shape(self, shape: 'rcfg.ResumableShape'):
        """Temporarily select the resumable shape (async / generator) the
        policy seams emit for, restoring the prior shape on exit."""
        prev = self._shape
        self._shape = shape
        try:
            yield
        finally:
            self._shape = prev

    def _is_generator_shape(self) -> bool:
        return self._shape is rcfg.ResumableShape.GENERATOR

    def gen_struct_name(self, func: TpyFunction,
                        record_name: str | None = None) -> str:
        """Resumable-frame struct name for the CURRENT function. Shape-aware:
        `__gen_<funcname>` (/ `__gen_<Record>_<funcname>`) for the generator
        shape, `__coro_<funcname>` (/ `__coro_<Record>_<funcname>`) for the
        async shape -- so the struct name reflects what it is (the
        `operator<<` repr uses the same `__gen_` convention). The grammar
        itself lives in
        `rcfg.frame_struct_name`, shared with every call site that embeds
        someone else's frame."""
        return rcfg.frame_struct_name(func.name, record_name, self._shape)

    def _frame_deep_const_verdict(
            self, func: TpyFunction,
            record_name: 'str | None') -> 'frozenset[int] | None':
        """The per-param deep-const verdict off the RAW fi (methods: the
        registry method fi; free defs: overloads[-1], the implementation).
        Drives the frame-field const spelling for pointer-repr tuple/union
        captures; the CFG-window const-set seeding must read the same
        source so alias-local classification agrees with the field."""
        if record_name:
            _ri = self.ctx.analyzer.registry.get_record(record_name)
            _mfi = _ri.get_method(func.name) if _ri else None
            return _mfi.const_borrow_params if _mfi else None
        _fis = self.ctx.analyzer.registry.get_function(func.name)
        return _fis[-1].const_borrow_params if _fis else None

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

        str / bytes params are captured OWNED in the frame
        (_CoroParamKind.OWNED_COPY -- the view is copied into std::string /
        tpy::bytes at construction) so they survive a suspension even when the
        arg is a temporary. Explicit view params (StrView/Span) stay borrow
        form (the caller opted into view semantics).
        """
        out: list[_CoroParam] = []
        # The per-param const verdict (addr-escape / readonly aware) lives on
        # the resolved FunctionInfo. An inferred-readonly method does NOT wrap
        # its params in ReadonlyType, so the factory field must consult the
        # verdict, not just the param type, to match the call site + body.
        _deep_const = self._frame_deep_const_verdict(func, record_name)
        if record_name:
            # Match the factory-site spelling (generator.py:1177, :1252):
            # convert dotted nested-class names to C++ scope syntax (`Outer.Inner`
            # -> `Outer::Inner`) before escaping, then template-qualify for a
            # generic class (`Box[T]` -> `Box<T>&`) -- the bare class name in
            # a type position outside the class body is ill-formed for
            # templates.
            cpp_record = escape_cpp_name(record_name.replace(".", "::"))
            record_tps = self._record_template_args(record_name)
            if record_tps:
                cpp_record = f"{cpp_record}<{', '.join(record_tps)}>"
            const_prefix = "const " if func.is_readonly else ""
            recv_type = f"{const_prefix}{cpp_record}"
            out.append(_CoroParam(
                cpp_name="__self",
                field_type=recv_type,
                ctor_param_type=recv_type,
                kind=_CoroParamKind.REF,
            ))
        for _pidx, (pname, ptype) in enumerate(func.params):
            cpp_name = escape_cpp_name(pname)
            ptype_inner = unwrap_ref_type(ptype)
            actual = unwrap_readonly(ptype_inner)
            kind = self._classify_param_kind(ptype)
            owned_init = ""
            if kind is _CoroParamKind.VALUE:
                # Value types (incl. explicit views like StrView) store their
                # own C++ type by value; str/bytes are OWNED_COPY, not here.
                field_type = ctor_type = self.types.type_to_cpp(ptype_inner)
            elif kind is _CoroParamKind.OWNED_COPY:
                # Owned storage field, but the factory/ctor take the borrow form
                # the call site passes (string_view / BytesView, or their
                # nullable optional<view>); ctor_init copies it in via
                # `owned_init` so it survives suspensions.
                ctor_type = ptype_inner.to_cpp_param_type()
                owned_init = view_owned_copy_init(ptype_inner, f"{cpp_name}_")
                # Owned field: str's owned form is std::string (its bare
                # type_to_cpp is the string_view *view*); bytes and the nullable
                # str|None / bytes|None forms are already owned via type_to_cpp.
                field_type = ("std::string" if is_str_type(ptype_inner)
                              else self.types.type_to_cpp(ptype_inner))
            elif kind is _CoroParamKind.TYPE_PARAM:
                # to_cpp_return / to_cpp_param_type already encode the
                # val_or_ref_t<T> / param_val_or_ref_t<T> traits (and
                # collapse to std::size_t for INT-kind params).
                field_type = ptype_inner.to_cpp_return()
                ctor_type = ptype_inner.to_cpp_param_type()
            elif kind is _CoroParamKind.STATIC_PROTOCOL:
                # Static-protocol param (e.g. `Own[Awaitable[T]]`): the
                # concrete operand type is deduced as an extra template
                # arg `T_<pname>` with a concept constraint, declared in
                # `_emit_template_header`. Field stores by value;
                # ctor/factory forward via T_<pname>&&.
                #
                # `T_{pname}` (raw, not escaped) keeps the template-arg
                # name aligned with `_protocol_template_parts` (which
                # declares the template arg) -- `_struct_name_templated`
                # reads the same field back to spell instantiations.
                # Using `cpp_name` here would diverge when `pname`
                # collides with a C++ keyword (e.g. `class` -> `class_`).
                field_type = ctor_type = protocol_param_template_name(pname)
            elif kind is _CoroParamKind.FN:
                # Concrete callable type deduced as `F_<pname>` (declared in
                # `_emit_template_header`); stored by value, forwarded via
                # `F_<pname>&&` -- same shape as STATIC_PROTOCOL.
                field_type = ctor_type = fn_param_template_name(pname)
            elif kind is _CoroParamKind.POINTER:
                if isinstance(actual, OptionalType) and actual.uses_pointer_repr():
                    field_type = ctor_type = ptype_inner.to_cpp_param_type()
                elif isinstance(actual, TupleType):
                    # Borrow form: std::tuple<..., T*> (readonly -> const T*).
                    # The pointers alias the caller; stored by value in the
                    # frame so they survive suspension. Deep-const when the
                    # param is `readonly[...]` OR the inferred verdict
                    # deep-consts it (mirrors the union arm below and the
                    # sync signature + call site; yield-escaped params are
                    # excluded by the verdict itself).
                    spell = ptype_inner
                    if (spell is actual and _deep_const is not None
                            and _pidx in _deep_const):
                        spell = ReadonlyType(actual)
                    field_type = ctor_type = spell.to_cpp_return()
                else:
                    # Non-value union: the pointer-variant borrow form
                    # (`::tpy::Union<A*, B*>`) is the shape every other param
                    # boundary uses -- ordinary functions, simple generators,
                    # plain locals. Storing it by value in the frame keeps the
                    # factory's signature in step with what the call site (and
                    # the emplace path) already build; a value-variant-by-ref
                    # field would be the lone divergence. Deep-const when the
                    # param is `readonly[...]` OR the const verdict deep-consts
                    # it (matches the call site + body, addr-escape aware).
                    is_readonly_param = ((actual is not ptype_inner)
                                         or (_deep_const is not None
                                             and _pidx in _deep_const))
                    field_type = ctor_type = (
                        self.types.type_to_cpp_const_ptr_variant(actual)
                        if is_readonly_param
                        else self.types.type_to_cpp_ptr_variant(actual))
            else:  # OWNED_VALUE / REF -- both spell the plain C++ type
                field_type = ctor_type = self.types.type_to_cpp(ptype_inner)
                # An explicit readonly[T] reference param captures const --
                # flows to the frame field, ctor, and both factory decls,
                # which all derive from this spelling.
                if (kind is _CoroParamKind.REF
                        and isinstance(ptype_inner, ReadonlyType)):
                    field_type = ctor_type = f"const {field_type}"
            out.append(_CoroParam(
                cpp_name=cpp_name,
                field_type=field_type,
                ctor_param_type=ctor_type,
                kind=kind,
                owned_copy_init=owned_init,
            ))
        return out

    def _classify_param_kind(self, ptype: 'TpyType') -> _CoroParamKind:
        """Decide the coro-frame storage kind for an async-def param.

        Single source of truth for the kind cascade: `_classify_params`
        derives the C++ spelling from it, and `_param_borrows` reads it to
        decide whether an rvalue-temporary arg bound to this param must be
        hoisted into the awaiter's frame (see `_lift_borrowed_rvalue_args`).
        """
        ptype_inner = unwrap_ref_type(ptype)
        actual = unwrap_readonly(ptype_inner)
        # str / bytes are captured owned in the frame (copied at construction)
        # so the view doesn't dangle across a suspension when the arg is a
        # temporary -- see is_owned_in_coro_frame (the sema borrow set excludes
        # the same set). Explicit view params (StrView/Span) stay borrow-form.
        if is_owned_in_coro_frame(ptype_inner):
            return _CoroParamKind.OWNED_COPY
        if isinstance(ptype_inner, TypeParamRef):
            return _CoroParamKind.TYPE_PARAM
        # Checked before the OptionalType-pointer-repr branch so
        # `Own[Awaitable[T]] | None` isn't mis-routed to POINTER --
        # `_protocol_template_parts` rejects the nullable shape instead.
        if self.functions.protocols.is_static_protocol_param(ptype):
            return _CoroParamKind.STATIC_PROTOCOL
        # Fn callable: concrete type is deduced as an `F_<pname>` template arg
        # (like a static protocol). Checked before is_value_type() -- a
        # CallableType would otherwise route through type_to_cpp(), which a
        # template-mode Fn cannot answer.
        if is_fn_type(actual):
            return _CoroParamKind.FN
        # A generic `T | None` slot lands here too, and the arm's spelling is
        # what it wants: `to_cpp_param_type()` renders the runtime's
        # per-instantiation form, which owns at a value T and points at a
        # reference one. Only the KIND's name is a pointer -- the borrow
        # question is answered per instantiation, on the substituted slot the
        # call site passes to `_param_borrows`.
        if isinstance(actual, OptionalType) and actual.uses_pointer_repr():
            return _CoroParamKind.POINTER
        if self.ctx.is_ptr_variant_union(actual):
            return _CoroParamKind.POINTER
        # A borrow-form tuple param (std::tuple<..., T*>) is held in the frame
        # by value-of-pointers so it aliases the caller across suspensions; a
        # VALUE-kind field would store the storage form (std::tuple<..., T>)
        # and silently copy the element where CPython shares it.
        if isinstance(actual, TupleType) and actual.has_pointer_repr_element():
            return _CoroParamKind.POINTER
        # Own[T] checked before is_value_type(): `OwnType.is_value_type()` is
        # True (Own[T] uses T&& at param boundaries) but the move-only
        # semantics need explicit forwarding, so it can't ride the VALUE path.
        if isinstance(ptype_inner, OwnType):
            return _CoroParamKind.OWNED_VALUE
        if ptype_inner.is_value_type():
            return _CoroParamKind.VALUE
        return _CoroParamKind.REF

    def _param_borrows(self, ptype: 'TpyType') -> bool:
        """True iff a param of this type is captured in the coro frame as a
        borrow (a reference/pointer into the arg) rather than by value -- the
        REF / POINTER kinds. An rvalue-temporary arg bound to such a param
        dangles once the suspending `case` block exits unless it is hoisted
        into a frame field that outlives the sub-coro.

        TYPE_PARAM (generic-async) is intentionally not treated as borrowing
        here: its value-vs-reference form resolves only at instantiation, so
        the borrowing rvalue-arg dangle for an object-typed `T` is left to the
        generic-async dangle tracked in BUGS.md (#292 M7).

        A generic `T | None` slot needs no such carve-out: its only caller
        (`_hoist_borrowed_args`) asks about the resolved callee's SUBSTITUTED
        slot, which is a value Optional at a value T (no hoist) and a
        pointer-repr one at a reference T (hoisted), so the answer is already
        per instantiation."""
        return self._classify_param_kind(ptype) in (
            _CoroParamKind.REF, _CoroParamKind.POINTER)

    def _frame_temp_slot(self, ptype: 'TpyType',
                         arg: 'TpyExpr') -> 'TpyType | None':
        """The OWNED type a TEMPORARY argument of the awaited sub-coro hoists
        into a slot of THIS frame, or None when nothing is owed.

        A frame never receives a temporary, so this is the same question the
        sync call sites ask and it is the same predicate:
        `frame_temp_arg_slot` decides. `_param_borrows` cannot answer for a
        borrowing-VIEW slot: the view's frame field is a VALUE-kind capture,
        so the kind cascade says by-value while the aliasing says borrow."""
        from ..thir.lower import frame_temp_arg_slot
        return frame_temp_arg_slot(arg, ptype, self.ctx.analyzer)

    def _view_backing_coerce(self, ptype: 'TpyType',
                             arg: 'TpyExpr') -> 'TpyCoerce | None':
        """The coerce node the lift re-seats when a hoisted temporary argument
        arrived through one (`::tpy::as_mut_span(<source>)`): the coerce keeps
        its render and only its SOURCE moves onto the frame slot.

        A STACKED coerce is safe by the lift's own shape rather than by
        witness: `_hoist_borrowed_args` captures `host.expr` BEFORE re-seating,
        so the hoisted decl's init is the inner coerce whole and only the
        outermost source name is replaced. No two-layer coerce at a view
        argument could be built to observe it, so the claim is reasoned from
        that capture, not pinned by a case."""
        if not isinstance(arg, TpyCoerce):
            return None
        return arg if self._frame_temp_slot(ptype, arg) is not None else None

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
            targ_name = protocol_param_template_name(pname)
            if proto.type_args:
                targs = ", ".join(t.to_cpp() for t in proto.type_args)
                parts.append(f"{concept_name}<{targs}> {targ_name}")
            else:
                parts.append(f"{concept_name} {targ_name}")
        return parts

    def _fn_template_parts(self, func: TpyFunction) -> list[str]:
        """Template-header parts for `Fn` callable params: a bare
        `typename F_<pname>` per param. The body's call site (`pred(x)`)
        constrains the type by use; the non-generator path adds a
        requires-clause for diagnostics, deferred here.
        """
        return [f"typename {fn_param_template_name(pname)}"
                for pname, ptype in func.params
                if is_fn_type(unwrap_readonly(unwrap_ref_type(ptype)))]

    def _record_template_args(self, record_name: str | None) -> tuple[str, ...]:
        """Type params of the enclosing record for a method, or () for a free
        function / method on a non-generic class. A method on `class Box[T]`
        contributes T to the coro struct's template header so the struct can
        reference `Box<T>` in the self-capture field."""
        if not record_name:
            return ()
        info = self.ctx.analyzer.registry.get_record(record_name)
        if info is None or not info.type_params:
            return ()
        return tuple(info.type_params)

    def _record_template_parts(self, record_name: str | None) -> list[str]:
        """Rendered template-header parts for the enclosing record's type
        params, mirroring `protocols.gen_record_template_parts` so out-of-class
        member-def headers spell the same constrained form as the class
        declaration (`Iterable<int32_t> T`, not bare `typename T`). Without
        this, an out-of-class definition's parameter-list is non-equivalent to
        the in-class declaration's and C++ rejects with a constraint
        mismatch."""
        if not record_name:
            return []
        info = self.ctx.analyzer.registry.get_record(record_name)
        if info is None or not info.type_params:
            return []
        return self.functions.protocols.gen_record_template_parts(
            list(info.type_params),
            info.type_param_bounds or {},
            info.type_param_kinds)

    def _is_templated_coro(self, func: TpyFunction,
                            record_name: str | None = None) -> bool:
        """True iff the coro/generator struct is a C++ template -- because
        of explicit `[T, ...]` type params, a static-protocol-typed param
        (`T_<pname>` template arg), OR (for a method) the enclosing record's
        type params. Templated structs must emit their poll/__next__ body +
        factory inline in the header; a non-template struct's body lands in
        the .cpp. Single source of truth for the header-vs-cpp placement
        decision across both async and generator shapes."""
        return (bool(func.type_params)
                or bool(self._protocol_template_parts(func))
                or bool(self._record_template_parts(record_name)))

    def _emit_template_header(self, out: "TextIO", func: TpyFunction,
                                *, indent: str = "",
                                record_name: str | None = None) -> bool:
        proto_parts = self._protocol_template_parts(func)
        fn_parts = self._fn_template_parts(func)
        record_parts = self._record_template_parts(record_name)
        if (not func.type_params and not proto_parts and not fn_parts
                and not record_parts):
            return False
        # Record's [T...] first so an outer Box<T> reads naturally; then the
        # method's own [U...]; then protocol-typed-param template args. The
        # combined-flat form is correct for free types (structs at namespace
        # scope) -- the coro/gen struct itself is a free `template<typename
        # T, typename U> struct __coro_...`. For an out-of-class member
        # function definition of a class template (`Box<T>::method`) where
        # the method is itself a template, C++ requires NESTED headers
        # instead -- see `_emit_member_template_headers`.
        parts = list(record_parts)
        parts.extend(f"typename {tp}" for tp in func.type_params)
        parts.extend(proto_parts)
        parts.extend(fn_parts)
        out.write(f"{indent}template <{', '.join(parts)}>\n")
        return True

    def _emit_member_template_headers(self, out: "TextIO",
                                       func: TpyFunction,
                                       *, record_name: str,
                                       indent: str = "") -> None:
        """Emit C++ template headers for the out-of-class definition of a
        member function of a class template: `template <record_tps>` then
        `template <method_type_params + proto_parts>`. Either level may
        collapse to no-op if that level has no params (e.g. a non-template
        method on a class template emits only the class header; a method
        template on a non-generic class emits only the method header). The
        nested form is the **only** C++-legal spelling for "member function
        template of a class template"; the flat form `_emit_template_header`
        produces declares a different entity and triggers
        no-declaration-matches at the in-class declaration."""
        record_parts = self._record_template_parts(record_name)
        if record_parts:
            out.write(f"{indent}template <{', '.join(record_parts)}>\n")
        proto_parts = self._protocol_template_parts(func)
        fn_parts = self._fn_template_parts(func)
        if func.type_params or proto_parts or fn_parts:
            parts_list = [f"typename {tp}" for tp in func.type_params]
            parts_list.extend(proto_parts)
            parts_list.extend(fn_parts)
            out.write(f"{indent}template <{', '.join(parts_list)}>\n")

    def _struct_name_templated(self, func: TpyFunction,
                                record_name: str | None = None) -> str:
        """Return the coro struct name suffixed with `<T1, T2, ...>` when the
        function is generic, else the bare name. Use this whenever the
        struct name appears in a type-name position (return types,
        out-of-line method qualifiers, parameter types) -- C++ rejects
        the injected-class-name there. The bare `gen_struct_name` is
        still correct inside the struct body (constructors) and for
        forward decls (`struct X;`).

        Template-arg order matches `_emit_template_header`: record's type
        params first (for a method on a generic class), then the function's
        own type params, then per-param `T_<pname>` static-protocol args.
        """
        bare = self.gen_struct_name(func, record_name)
        cparams = self._classify_params(func, record_name)
        # Order must match `_emit_template_header`: protocol args then Fn args.
        extras = [p.field_type for p in cparams
                  if p.kind is _CoroParamKind.STATIC_PROTOCOL]
        extras += [p.field_type for p in cparams
                   if p.kind is _CoroParamKind.FN]
        all_args = (list(self._record_template_args(record_name))
                    + list(func.type_params) + extras)
        if not all_args:
            return bare
        return f"{bare}<{', '.join(all_args)}>"

    def _default_suffix(self, func: TpyFunction, param_index: int) -> str:
        """`" = <cpp>"` for a param whose default C++ can express, else `""`.

        The one place the resumable emitter spells a default, so the factory
        declaration and the frame constructor cannot drift -- a call that
        omits an argument lands on one or the other. `param_index` indexes
        `func.params`, which has no `__self` cparam, so a caller walking
        cparams subtracts that offset first.
        """
        defaults = func.defaults or []
        ptype = func.params[param_index][1]
        if not default_emittable(defaults, param_index, len(func.params),
                                 ptype, func.params, func.is_method):
            return ""
        return f" = {default_to_cpp(self.ctx, defaults[param_index], ptype)}"

    def _emit_params_decl(self, func: TpyFunction, *,
                          emit_defaults: bool = False) -> str:
        # Defaults belong on the factory's forward declaration only -- the
        # definition would redefine them (C++ error). Aligned to func.params
        # (a free-function factory has no __self cparam).
        parts: list[str] = []
        for i, cp in enumerate(self._classify_params(func)):
            decl = cp.factory_param_decl()
            if emit_defaults:
                decl += self._default_suffix(func, i)
            parts.append(decl)
        return ", ".join(parts)

    def _emit_method_params_decl(self, method: TpyFunction, record_name: str,
                                 *, emit_defaults: bool = False) -> str:
        """User-facing factory signature params for a generator/async *method*
        (`Z::voices(...)`), derived from the SAME `_classify_params` the frame
        field and ctor use -- so the signature's per-param const-ness (union
        deep-const per the verdict, ref params mutable) matches the field by
        construction. `__self` is implicit via `*this`, so it's dropped.
        Mirrors `_emit_params_decl` for free functions.

        `emit_defaults` is True only at the in-class declaration -- for a
        method that IS the canonical first declaration, and C++ rejects a
        default repeated on the out-of-line definition."""
        parts: list[str] = []
        for i, cp in enumerate(p for p in self._classify_params(method, record_name)
                               if p.cpp_name != "__self"):
            decl = cp.factory_param_decl()
            if emit_defaults:
                decl += self._default_suffix(method, i)
            parts.append(decl)
        return ", ".join(parts)

    def _ret_cpp(self, func: TpyFunction) -> str:
        """The coro's Poll payload C++ type, per `async_return_form`:
        borrow-contract returns (bare reference types and pointer-repr
        Optionals) use pointer form, matching the sync `to_cpp_return`
        convention -- so the await binding aliases the source rather than
        copying. The sync dangling-return check (plus the async Own-param
        root rule) gates what may be returned as a borrow: sources root
        in caller-durable storage that outlives the frame. Generic `-> T`
        defers the split to instantiation via `val_or_ptr_t<T>` (value ->
        T, object -> T*), mirroring sync's `val_or_ref_t`. Pointer-variant
        Union returns share the root cause but their await-result consumer
        (frame-slot binding + isinstance narrowing) is not yet borrow-form
        aware, and recursive-union wrappers have no pointer spelling --
        both keep storage form (see BUGS.md)."""
        rt = unwrap_ref_type(func.return_type)
        form = async_return_form(func.return_type)
        if form is AsyncReturnForm.BORROW:
            bare = unwrap_readonly(unwrap_send_sync(rt))
            if isinstance(bare, OptionalType):
                return rt.to_cpp_return()
            const_pfx = "const " if isinstance(rt, ReadonlyType) else ""
            return f"{const_pfx}{self.types.type_to_cpp(bare)}*"
        if form is AsyncReturnForm.TRAIT:
            bare = unwrap_readonly(unwrap_send_sync(rt))
            trait = ("val_or_cptr_t" if isinstance(rt, ReadonlyType)
                     else "val_or_ptr_t")
            return f"::tpy::{trait}<{self.types.type_to_cpp(bare)}>"
        return self.types.type_to_cpp(rt)

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
        self._emit_template_header(out, func, record_name=record_name)
        out.write(f"struct {struct_name};\n")
        return True

    def gen_factory_forward_decl(self, out: "TextIO", func: TpyFunction) -> bool:
        return_type_name = self._struct_name_templated(func)
        self._emit_template_header(out, func)
        # Default arg values live on this forward decl (the canonical first
        # declaration); `gen_factory`'s definition omits them.
        params = self._emit_params_decl(func, emit_defaults=True)
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
        state = rcfg.resumable_state(func)
        if state.lifted_body is not None:
            return state.lifted_body
        lifted = self._lift_nested_awaits(func, func.body)
        # Runs on the await-normalized body: every await now sits in a
        # top-level statement, so each INLINE await's call args are in a
        # known position to hoist (a nested-await arg has already become a
        # stable hoisted-local name and is left alone).
        lifted = self._lift_borrowed_rvalue_args(func, lifted)
        state.lifted_body = lifted
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
        return self._map_compound_subbodies(func, stmt, self._lift_nested_awaits)

    def _map_compound_subbodies(
            self, func: TpyFunction, stmt: TpyStmt,
            transform: 'Callable[[TpyFunction, list[TpyStmt]], list[TpyStmt]]',
    ) -> TpyStmt:
        """Apply `transform(func, body)` to each statement-list sub-body of a
        compound `stmt` (its dataclass fields plus try-handler bodies). Return
        `stmt` unchanged when nothing moved -- preserving its id for the
        analyzer's id(stmt)-keyed tables -- or a shallow copy with the
        rewritten sub-bodies otherwise. Does not mutate the input."""
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
                lifted = transform(func, v)
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
                lifted_body = transform(func, h.body) if h.body else h.body
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
            # One-shot by construction: created here, consumed by `stmt` (the
            # single replacement below). Records it as a movable source so a
            # tuple-unpack reading it moves elements out instead of copying.
            # INVARIANT: only OWNED results may be added here -- an await
            # result is always owned, and `owning_generator_tuple_locals`
            # relies on that to give a reference-element lift tuple owning
            # (not borrow) frame storage. Do not add a borrow/aliasing source.
            rcfg.resumable_state(func).one_shot_lift_names.add(name)
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
        return rcfg.resumable_state(func).next_lift_id

    def _bump_lift_id(self, func: TpyFunction) -> None:
        rcfg.resumable_state(func).next_lift_id += 1

    # -- Borrowed-rvalue-arg lift (runs after the await-lift pass) ------------

    def _lift_borrowed_rvalue_args(self, func: TpyFunction,
                                    body: list[TpyStmt],
                                    in_loop: bool = False) -> list[TpyStmt]:
        """Hoist rvalue-temporary arguments of INLINE-mode await calls into
        frame-backed hoisted locals so the sub-coro's borrow doesn't dangle
        across the suspension.

            await f(Dog("x"))          # f(a: Dog | Cat)
        becomes
            __coro_arg_0 = Dog("x")
            await f(__coro_arg_0)

        The sub-coro frame's `variant<Cat*, Dog*>` (or `T*` for pointer-form
        Optional, or `T&` for a plain non-value ref param) then points at
        `__coro_arg_0` -- a frame field outliving the sub-coro -- instead of
        a `__tmp` flushed inside the suspending `case` block, which dies at
        the `continue`. Without the lift the pointer/variant shapes are
        silent UB and the plain-ref shape is a hard C++ error.

        A stable-lvalue arg (a name or field chain) is left in place: its
        address already persists across the suspension, and copying it would
        break mutation-through-borrow and reject @nocopy elements.

        Runs after `_lift_nested_awaits`, so every await is already in a
        top-level statement; recursion into compound sub-bodies mirrors that
        pass. The await half is self-gating for generators (no awaits -> no
        INLINE calls); `_hoist_frame_factory_args` then covers every OTHER
        generator/coro factory call in the body -- a `for` head, a handle bound
        to a local, the escaping `create_task(f(rvalue))` form -- whose
        argument the uniform frame-temp rule would otherwise have named in the
        state's `case` block, which the handle outlives.

        A `Task` that outlives the whole enclosing frame is a further hazard
        this pass cannot answer (`BUGS.md#create-task-ref-param-escape`): a
        frame field is as long-lived as this body, no longer.

        `in_loop` says whether the statements can RE-EXECUTE within one run of
        this body. One field per call SITE is only enough while at most one
        handle built there is alive at a time, so a site whose handle outlives
        the iteration is rejected instead of seated (see
        `_hoist_frame_factory_args`)."""
        out: list[TpyStmt] = []
        for stmt in body:
            hoisted = self._hoist_borrowed_args(func, stmt)
            # The factory hoist runs on the await hoist's OUTPUT: an inline
            # await's arguments are already frame-local names by then, so the
            # two passes never name the same argument twice.
            out.extend(hoisted[:-1])
            out.extend(
                self._hoist_frame_factory_args(func, hoisted[-1], in_loop))
            # Recurse compound bodies AFTER the surface hoists: they mutate
            # only the host statement's own call arg lists, never sub-bodies,
            # so the passes never touch the same node. A loop's OWN head was
            # hoisted at the enclosing depth just above; only its sub-bodies
            # re-execute.
            sub_in_loop = in_loop or isinstance(out[-1], (TpyForEach, TpyWhile))
            out[-1] = self._map_compound_subbodies(
                func, out[-1],
                partial(self._lift_borrowed_rvalue_args, in_loop=sub_in_loop))
        return out

    def _hoist_borrowed_args(self, func: TpyFunction,
                              stmt: TpyStmt) -> list[TpyStmt]:
        """If `stmt` hosts a top-level INLINE await call, hoist its borrowed
        rvalue args into preceding vardecls (mutating the call's arg list in
        place to reference the hoisted names). Returns [preceding vardecls...,
        stmt]; [stmt] when nothing to hoist."""
        await_node = rcfg._top_level_await_in(stmt)
        if await_node is None or await_node.awaited_async_func_name is None:
            return [stmt]
        call = await_node.value
        if not isinstance(call, (TpyCall, TpyMethodCall)):
            return [stmt]
        fi = call.resolved_function_info
        if fi is None:
            return [stmt]
        pre: list[TpyStmt] = []
        for i, arg in enumerate(call.args):
            # Varargs (arg_index past the declared params) are a separate
            # dangle class tracked in BUGS.md; leave them for the *args path.
            if i >= len(fi.params):
                break
            # A BORROWING-VIEW slot is passed by value, but the value IS a
            # borrow the sub-coro's frame keeps for its whole life -- the same
            # standing the REF / POINTER kinds have. What must outlive the
            # suspension is then the STORAGE the view aliases, so the lift
            # targets the coerce's source and leaves the view built over the
            # hoisted local (`::tpy::as_mut_span((*__coro_arg_0))`).
            host = self._view_backing_coerce(fi.params[i].type, arg)
            temp_slot = self._frame_temp_slot(fi.params[i].type, arg)
            if (host is None and temp_slot is None
                    and not self._param_borrows(fi.params[i].type)):
                continue
            source = host.expr if host is not None else arg
            # `None` lowers to nullptr (pointer-form Optional) or
            # `std::monostate` (pointer-variant union) -- a by-value slot, not
            # a borrow, so there is no temp to outlive the suspension.
            if isinstance(source, TpyNoneLiteral):
                continue
            if not (self.ctx.is_rvalue_source(source)
                    or self.ctx.is_temporary_expr(source)):
                continue
            # The frame slot takes the source's OWNED type where the uniform
            # frame-temp rule named one (a str/bytes argument owns its buffer
            # in the slot however the param spells it).
            arg_t = (temp_slot if temp_slot is not None
                     else self.ctx.get_expr_type(source))
            if arg_t is None:
                raise CodeGenError(
                    "await arg has no analyzed type (borrowed-arg lift)",
                    loc=stmt.loc)
            self._seat_arg_on_frame_local(func, call, i, fi.params[i].type,
                                          source, host, arg_t, pre)
        pre.append(stmt)
        return pre

    def _seat_arg_on_frame_local(self, func: TpyFunction, call, i: int,
                                 ptype: 'TpyType', source: TpyExpr,
                                 host: 'TpyCoerce | None', arg_t: 'TpyType',
                                 pre: list[TpyStmt]) -> None:
        """Move `source` onto a `__coro_arg_N` local of THIS frame
        (`_frame_local_for`) and point argument `i` of `call` at it.

        A coerce-shaped argument keeps its coerce and only its SOURCE moves
        (`::tpy::as_mut_span(__coro_arg_0)`); an argument with no coerce
        becomes the local itself."""
        arg_t = unwrap_ref_type(arg_t)
        # A tuple arg's analyzed type can carry IntLiteral elements and
        # per-element Own provenance, which would demote the hoisted frame
        # field to a value tuple (or fail to render). The hoisted local
        # exists to back the param's borrow, so give it the declared param
        # slot shape -- the frame field then takes the borrow form and the
        # sub-coro's pointer slots stay valid across the suspension.
        lift_ptype = unwrap_readonly(unwrap_ref_type(ptype))
        if isinstance(arg_t, TupleType) and isinstance(lift_ptype, TupleType):
            arg_t = lift_ptype
        replacement = self._frame_local_for(func, source, arg_t, pre)
        if host is not None:
            host.expr = replacement
        else:
            call.args[i] = replacement

    def _frame_local_for(self, func: TpyFunction, source: TpyExpr,
                         local_t: 'TpyType',
                         pre: list[TpyStmt]) -> TpyName:
        """Declare `source` as a fresh `__coro_arg_N` local of THIS frame and
        return the name that reads it back.

        The local is registered in `generator_locals`, so its storage is a
        field of the enclosing frame and lives as long as the frame does --
        which is what a hoist inside a resumable body has to mean. A local of
        the enclosing BLOCK would be a `case`-block local, and every handle
        this hoist exists to feed (a sub-coro field, a `__for_src` slot, a
        `Task`) outlives that block."""
        name = f"__coro_arg_{rcfg.resumable_state(func).next_arg_lift_id}"
        rcfg.resumable_state(func).next_arg_lift_id += 1
        # loc=None: the hoisted decl is a synthesized sub-step of the host
        # statement, not its own source line, so it must not re-emit the
        # host's source comment (which the host statement still emits).
        pre.append(TpyVarDecl(name=name, type=local_t, init=source, loc=None))
        if func.generator_locals is None:
            func.generator_locals = []
        func.generator_locals.append((name, local_t))
        replacement = TpyName(name=name, loc=source.loc)
        self.ctx.analyzer.ctx.set_expr_type(replacement, local_t)
        return replacement

    def _frame_factory_calls(
            self, func: TpyFunction,
            stmt: TpyStmt) -> 'list[tuple[TpyExpr, bool]]':
        """The generator/coroutine FACTORY calls in `stmt` that run
        unconditionally and exactly once when control reaches the statement,
        each paired with whether its HANDLE can outlive the statement.

        That is the whole admission for the hoist below: a preceding decl
        preserves evaluation order only where the call was going to run anyway,
        once. So the descent starts at the statement slots evaluated on entry
        (a `for` head, an init, an assigned value, a discarded expression) and
        walks only through coercions, `await` operands and ARGUMENT lists --
        never into a ternary arm, a `bool` operator's right side, a
        comprehension body or a nested def, where the sub-expression is
        conditional or repeated. A `return` / `yield` value is out for a
        different reason: the handle LEAVES this frame, so seating its argument
        here would pin the argument to a frame that dies first.

        The callee test is the one the lowering row uses (`borrowing_frame_
        callee`, plus an async def), so the hoist and the row cannot disagree
        about which callee keeps an argument past the statement.

        The paired flag is what the ONE-FIELD-PER-SITE seat below needs: the
        field is safe to re-emplace only where no handle built at this site is
        still alive. The positions that consume the handle where it stands are
        a `for` head (the loop statement drains and destroys the iterator), an
        `await` operand (the statement runs the coroutine to completion), a
        bare discard (the temporary dies at the semicolon), and a bind whose
        name is only ever drained -- one slot, re-assigned, so at most one
        handle is live. Every other position -- an argument of another call
        (`create_task(...)`, and through it `tasks.append(...)`), a bind whose
        name is copied elsewhere -- hands the handle on."""
        # The BIND answer walks the whole body, so it is a thunk: only a root
        # that turns out to host a factory call ever asks for it.
        root: 'TpyExpr | None' = None
        bind_escapes: 'Callable[[], bool]' = lambda: False
        if isinstance(stmt, TpyForEach):
            root = stmt.iterable
        elif isinstance(stmt, TpyVarDecl):
            root = stmt.init
            bind_escapes = partial(self._handle_name_escapes, func, stmt.name)
        elif isinstance(stmt, TpyAssign):
            root = stmt.value
            target = peel_coerce(stmt.target)
            bind_escapes = (
                partial(self._handle_name_escapes, func, target.name)
                if isinstance(target, TpyName) else (lambda: True))
        elif isinstance(stmt, TpyTupleUnpack):
            root = stmt.value
            targets = stmt.targets
            bind_escapes = lambda: any(
                t is None or self._handle_name_escapes(func, t)
                for t in targets)
        elif isinstance(stmt, TpyExprStmt):
            root = stmt.expr
        # `None` in a pair means "whatever the bind answers"; a nested position
        # has already decided (an argument hands the handle on, an `await`
        # consumes it) and overrides the bind either way.
        found: 'list[tuple[TpyExpr, bool | None]]' = []

        def visit(e: 'TpyExpr | None', escapes: 'bool | None') -> None:
            if e is None:
                return
            if isinstance(e, TpyCoerce):
                visit(e.expr, escapes)
                return
            if isinstance(e, TpyAwait):
                visit(e.value, False)
                return
            if not isinstance(e, (TpyCall, TpyMethodCall)):
                return
            fi = e.resolved_function_info
            if fi is not None and (borrowing_frame_callee(fi) or fi.is_async):
                found.append((e, escapes))
            if isinstance(e, TpyMethodCall):
                visit(e.obj, escapes)
            for a in e.args:
                visit(a, True)

        visit(root, None)
        if not any(e is None for _, e in found):
            return [(c, bool(e)) for c, e in found]
        bound = bind_escapes()
        return [(c, bound if e is None else e) for c, e in found]

    def _handle_name_escapes(self, func: TpyFunction, name: str) -> bool:
        """Whether a generator/coroutine handle bound to `name` can be carried
        out of the statement that builds it.

        The bind itself is not: `name` is ONE slot, so a rebind on the next
        iteration destroys the previous handle and at most one is ever live --
        which is exactly what one hoisted field per call site can serve. What
        carries a handle out is a use that copies or moves it somewhere
        longer-lived (an argument, a container element, a return). The two
        DRAINING uses -- `await name` and `for ... in name` -- consume the
        handle where it stands, so they are not escapes; a rebind of `name`
        itself is not one either."""

        def expr_escapes(e: 'TpyExpr | None') -> bool:
            if e is None:
                return False
            if isinstance(e, TpyName):
                return e.name == name
            if isinstance(e, TpyAwait):
                inner = peel_coerce(e.value)
                if isinstance(inner, TpyName) and inner.name == name:
                    return False
            return any(expr_escapes(c)
                       for c in (e.children() if hasattr(e, "children") else ()))

        def body_escapes(body: 'list[TpyStmt]') -> bool:
            for s in body:
                skip: 'TpyExpr | None' = None
                if isinstance(s, TpyForEach):
                    head = peel_coerce(s.iterable)
                    if isinstance(head, TpyName) and head.name == name:
                        skip = s.iterable
                elif isinstance(s, TpyAssign):
                    target = peel_coerce(s.target)
                    if isinstance(target, TpyName) and target.name == name:
                        skip = s.target
                for e in (s.exprs() if hasattr(s, "exprs") else ()):
                    if e is not skip and expr_escapes(e):
                        return True
                if any(body_escapes(b) for b in s.sub_bodies()):
                    return True
                if isinstance(s, TpyTry):
                    for h in s.handlers:
                        if body_escapes(h.body):
                            return True
            return False

        return body_escapes(func.body)

    def _hoist_frame_factory_args(self, func: TpyFunction, stmt: TpyStmt,
                                  in_loop: bool = False) -> list[TpyStmt]:
        """Seat every temporary argument of a generator/coro factory call in
        `stmt` on a frame local, and return [decls..., stmt].

        The uniform frame-temp rule names such an argument at the enclosing
        statement's flush point; inside a resumable body that flush point is
        the state's `case` block, which the handle the factory returns
        outlives -- so the name has to be a frame field instead. Same shape
        predicate as the lowering row (`frame_temp_arg_slot`), so an argument
        named here is exactly one the row would otherwise have flushed into the
        `case` block, and the row then sees a plain name and does nothing.

        There is one field per call SITE, so a site that can re-execute while
        an earlier handle is still alive would hand every one of those handles
        the LAST value written. That is the `create_task(f(temp))`-in-a-loop
        shape, and no per-site field can serve it -- the seat is refused
        instead of silently aliasing (`_reject_shared_loop_seat`)."""
        pre: list[TpyStmt] = []
        for call, escapes in self._frame_factory_calls(func, stmt):
            fi = call.resolved_function_info
            shared = in_loop and escapes
            recv = self._factory_receiver_to_seat(func, call)
            if recv is not None:
                if shared:
                    self._reject_shared_loop_seat(
                        fi, recv, "the receiver",
                        ", or call the method on a named receiver")
                recv_t = self._frame_seat_type(recv)
                call.obj = self._frame_local_for(func, recv, recv_t, pre)
            for i, arg in enumerate(call.args):
                # Varargs (past the declared params) are a separate dangle
                # class tracked in BUGS.md; leave them for the *args path.
                if i >= len(fi.params):
                    break
                ptype = fi.params[i].type
                temp_slot = self._frame_temp_slot(ptype, arg)
                if temp_slot is None:
                    continue
                host = self._view_backing_coerce(ptype, arg)
                source = host.expr if host is not None else arg
                if shared:
                    self._reject_shared_loop_seat(
                        fi, source, f"argument '{fi.params[i].name}'",
                        ", or declare the parameter 'Own[...]' so the callee "
                        "owns its copy")
                self._seat_arg_on_frame_local(func, call, i, ptype, source,
                                              host, temp_slot, pre)
        pre.append(stmt)
        return pre

    def _reject_shared_loop_seat(self, fi, source: TpyExpr, what: str,
                                 remedy: str) -> 'NoReturn':
        """Refuse a temporary whose seat would be shared by several live
        handles of the same loop.

        The seat is a field of the enclosing frame, one per call site, so the
        next iteration overwrites it while the handle the previous iteration
        handed on (a `Task`, a stored generator) still borrows it. Naming the
        temporary in the loop body does not help -- a local of a resumable
        body is a frame field too -- so the remedy the message gives is
        storage that outlives the loop."""
        raise CodeGenError(
            f"cannot use a temporary as {what} of '{fi.name}' here: the "
            f"handle this call creates outlives the loop iteration, so every "
            f"handle the loop creates would borrow one shared slot of the "
            f"enclosing frame and see the last iteration's value. Keep each "
            f"value in storage that outlives the loop (append it to a list "
            f"declared before the loop and pass that element)" + remedy,
            loc=getattr(source, "loc", None))

    def _roots_at_temp(self, e: TpyExpr) -> bool:
        """Whether `e` reads a field / element out of a TEMPORARY -- storage
        the enclosing statement destroys. A chain rooted at a name (or at a
        subscript of one) is not: only the root's own lifetime is in
        question, and the access itself borrows into it."""
        root = peel_coerce(e)
        while isinstance(root, (TpyFieldAccess, TpySubscript)):
            root = peel_coerce(root.obj)
        return (root is not peel_coerce(e)
                and materializing_temp_source(root, self.ctx.analyzer))

    def _frame_seat_type(self, recv: TpyExpr) -> 'TpyType':
        """The frame-field type a seated factory receiver takes."""
        recv_t = self.ctx.analyzer.get_expr_type(recv)
        if recv_t is None:
            raise CodeGenError(
                "factory receiver has no analyzed type (receiver lift)",
                loc=getattr(recv, "loc", None))
        return unwrap_own(unwrap_readonly(
            unwrap_ref_type(unwrap_send_sync(recv_t))))

    def _factory_receiver_to_seat(self, func: TpyFunction,
                                  call) -> 'TpyExpr | None':
        """The TEMPORARY RECEIVER of a generator/coro factory method that must
        be seated on a frame local -- the receiver twin of the argument hoist
        above. `None` when the receiver already outlives the frame.

        The frame keeps the receiver as `<Class>&` for the handle's whole
        life, exactly as it keeps a borrowed argument, so one invariant covers
        both: a frame never receives a temporary. Inside a resumable body the
        receiver would otherwise be named in the state's `case` block
        (`Summer __tmp_1 = Summer(200);` -- the `method.gen_recv_temp` row,
        which materializes at the CONSUMING position) or not named at all (the
        async route binds `const Summer&` straight to the prvalue), and the
        handle outlives both.

        Admission is the shape the CONSUMING route already accepts, so the
        hoist removes a dangle without moving the accepted set: the ctor-rvalue
        slice for a generator factory -- hoisting a broader shape would make
        `for v in make_summer(1).pair(xs)` compile here while the identical
        line still rejects in a sync caller -- and any materializing rvalue for
        an async one, whose route gates no receiver shape at all (every rvalue
        receiver there compiles today and dangles). The hoisted receiver is a
        NAME, so the `gen_recv_temp` row no longer fires and no second temp is
        built.

        A receiver the seat cannot take but that BORROWS INTO a temporary --
        `make_pair(a, b).left`, a field or element read out of an owner the
        statement destroys -- is rejected on the async route rather than left
        to bind `const Summer&` to the dying owner. Seating the owner instead
        would widen what the language accepts (the generator route rejects the
        same receiver at lowering, `method.fi_kind`, and sema rejects it at the
        bound and awaited positions), so the routes are made to agree on the
        reject. A chain rooted at a NAME is untouched: its storage already
        outlives the statement."""
        if not isinstance(call, TpyMethodCall):
            return None
        analyzer = self.ctx.analyzer
        recv = call.obj
        fi = call.resolved_function_info
        is_async = fi is not None and fi.is_async
        if not materializing_temp_source(recv, analyzer):
            if is_async and self._roots_at_temp(recv):
                raise CodeGenError(
                    f"receiver of the async method '{fi.name}' must be a "
                    f"stable lvalue (a local, parameter, or field chain "
                    f"rooted at one) or a temporary this frame can seat -- "
                    f"the coroutine captures it by reference for the handle's "
                    f"whole life, and this one reads out of a temporary that "
                    f"dies at the end of the statement. Bind the owner to a "
                    f"local first: `r = <expr>; ... r.field.{fi.name}(...)`",
                    loc=getattr(recv, "loc", None))
            return None
        if not is_async:
            from ..thir.lower import gen_recv_ctor_temp
            if not gen_recv_ctor_temp(recv, analyzer):
                return None
        return recv

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

    # -- Frame-local placement -------------------------------------------------

    def _frame_layout(self, func: TpyFunction) -> 'rcfg.FrameLayoutPlan':
        """Get-or-build the frame-local placement plan for this body: one
        `FrameLocalLayout` verdict per `func.generator_locals` name.

        Must run after the for/with/alias prescans and the await/arg lifting
        have populated `ResumableFuncState` (every caller sits downstream of
        `_build_resumable_cfg`). Arm PRECEDENCE is load-bearing: pointer-form
        is checked before `is_value_type` because a pointer-form `__for_tup`
        holder is itself a value tuple; borrow-tuple excludes `__for_tup_*`
        holders (their form is the loop machinery's call) unless the loop
        classified them borrow-tuple explicitly.
        """
        state = rcfg.resumable_state(func)
        if state.frame_layout is not None:
            return state.frame_layout

        owning_str = state.with_owning_str_targets
        owning_tuple_locals = self.ctx.owning_generator_tuple_locals(func)
        pointer_form_names: set[str] = set()
        borrow_tuple_names: set[str] = set()
        # Loop vars whose field payload is spelled from the iteration
        # source; the trait decides alias-vs-own, so no TPy-side form.
        source_form_fields: dict[str, str] = {}
        # Loop vars whose `&(*it)` is a `const T*` (const-rooted source or a
        # readonly element); joins the statement-level const aliases below so
        # both reach the same `const` verdict.
        const_loop_vars: set[str] = set()
        for info in state.for_loop_info.values():
            if info.pointer_form_loop_var is not None:
                pointer_form_names.add(info.pointer_form_loop_var)
                if info.pointer_form_is_const:
                    const_loop_vars.add(info.pointer_form_loop_var)
            pointer_form_names.update(info.pointer_form_unpack_targets)
            if info.loop_var_field is not None:
                name, payload = info.loop_var_field
                source_form_fields[name] = payload
            if info.borrow_tuple_loop_var is not None:
                borrow_tuple_names.add(info.borrow_tuple_loop_var)
        # A `with ... as t` target joins the same family for the same reason: its
        # payload is spelled from `__enter__()` (`with_enter_t<CM>`) because
        # alias-vs-own is not decidable here either.
        source_form_fields.update(state.with_target_payloads)
        # Statement-level borrow aliases (single-assign / tuple-unpack)
        # also get a `T*` field rather than an owning frame_slot<T>. The
        # alias model belongs to the resumable FRAME (a `T*` field kept
        # live across suspensions); a simple-peephole generator keeps its
        # locals on the lambda stack, so its plan must classify with the
        # empty set -- running the prescan here would change its verdicts.
        if not GeneratorCodegen.is_simple_generator(func):
            pointer_form_names.update(self._classify_pointer_alias_locals(func))
        const_aliases = state.const_pointer_alias_locals | const_loop_vars

        bindings: dict[str, rcfg.FrameLocalLayout] = {}
        for lname, ltype in (func.generator_locals or []):
            ltype_inner = unwrap_ref_type(ltype)
            # A protocol-typed local has no concrete C++ backing -- only
            # captured params carry the deduced template arg `T_<pname>`. A
            # *single-assignment* alias of a bare protocol param (`xs = it`)
            # is forwarded to that param by sema (see
            # `_extract_proto_param_forwarding`) and never reaches here.
            # What lands here is the unforwarded remainder -- chiefly a
            # *reassigned* alias -- which has no single backing param. The
            # verdict is a kind (not an error) so non-frame consumers can
            # classify; rendering a frame FIELD for it raises at the struct
            # emit.
            payload: str | None = None
            effective_type: 'TpyType | None' = None
            if self.functions.protocols.is_static_protocol_param(ltype_inner):
                kind = rcfg.FrameLocalKind.PROTOCOL
            elif lname in source_form_fields:
                # Loop var whose alias-vs-own choice belongs to C++: the
                # payload is spelled from the source's iteration protocol,
                # and frame_slot's specializations supply either form. Comes
                # before the type-directed arms below -- they would re-derive
                # a form from the TPy element type, which is exactly what
                # this field exists to stop doing.
                kind = rcfg.FrameLocalKind.SOURCE_FORM_SLOT
                payload = source_form_fields[lname]
            elif lname in owning_str:
                # `with X() as label:` -- `__enter__` returns by value;
                # storing the view across suspensions would dangle. Use
                # owning storage. See `_prescan_with_stmts`.
                kind = rcfg.FrameLocalKind.OWNED_STR
            elif lname in pointer_form_names:
                # Pointer-form alias: a for-loop var (non-value element
                # over a stable source) or a tuple-unpack target bound to
                # a non-value container member. Stored as `T*` (alias the
                # live element) rather than `frame_slot<T>` (value-copy),
                # so mutations propagate and resume preserves aliasing;
                # also works for @nocopy / move-only elements. Bound by
                # address in `_emit_async_for_advance` / the tuple-unpack
                # emit.
                kind = rcfg.FrameLocalKind.PTR_ALIAS
            elif lname in owning_tuple_locals:
                # OWNING pointer-repr tuple local: the frame must hold the
                # element storage (emplace writes, `(*name)` reads) -- a
                # borrow `std::tuple<..., T*>` field can't own, and the
                # owning rvalue can't be address-taken into it. A MIXED tuple
                # has no fully-owned storage form to hold: its borrowed
                # element must keep pointing at the caller's object, so its
                # payload is the mixed render.
                #
                # The ownership axis reads off the EFFECTIVE type: a literal
                # init's per-element verdict carries no `Own` in the declared
                # type (there is no user spelling for it), so the oracle hands
                # the Own-wrapped image back and every arm below keys on that.
                eff = owning_tuple_locals[lname] or ltype_inner
                effective_type = eff
                if isinstance(eff, TupleType) and eff.is_mixed_own():
                    kind = rcfg.FrameLocalKind.MIXED_TUPLE_SLOT
                    payload = self.types.tuple_borrow_cpp(eff)
                else:
                    kind = rcfg.FrameLocalKind.OWNING_TUPLE_SLOT
            elif (isinstance(ltype_inner, TupleType)
                    and ltype_inner.has_pointer_repr_element()
                    and (not lname.startswith("__for_tup_")
                         or lname in borrow_tuple_names)):
                # Borrow-form tuple local (std::tuple<..., T*>): a value-form
                # field would copy the element across the suspension (the
                # silent-copy divergence). Pointers default-construct to
                # null, so no frame_slot wrapper is needed.
                kind = rcfg.FrameLocalKind.BORROW_TUPLE
            elif (isinstance(ltype_inner, OwnType)
                    and not unwrap_own(ltype_inner).is_value_type()
                    and not is_dyn_protocol(
                        unwrap_readonly(ltype_inner.wrapped))):
                # `Own[T]` over a REFERENCE type reports as a value here, but the
                # object it names is built at the BINDING, later than the frame.
                # A bare field would default-construct at frame creation and then
                # assign -- which is not the object's real constructor, and is
                # deleted outright once T holds a non-default-constructible
                # member (a `Box`, `Rc`, `Mutex`). The slot's placement-new runs
                # the actual constructor at the actual construction point.
                #
                # A coroutine/adapter handle is the exception: `Own[@dynamic P]`
                # renders as a `std::optional<coro>` / `unique_ptr` that already
                # default-constructs empty and takes the value by assignment, so
                # a slot around it would just double-wrap.
                kind = rcfg.FrameLocalKind.FRAME_SLOT
            elif ltype_inner.is_value_type():
                kind = rcfg.FrameLocalKind.VALUE
            elif (isinstance(ltype_inner, OptionalType)
                    and ltype_inner.uses_pointer_repr()):
                # Pointer-repr Optional: bare `T* = nullptr` aliases the
                # source and uses nullptr as both "uninitialized" and
                # "None"; no outer `std::optional<...>` wrap.
                kind = rcfg.FrameLocalKind.OPT_PTR
            else:
                kind = rcfg.FrameLocalKind.FRAME_SLOT
            bindings[lname] = rcfg.FrameLocalLayout(
                kind=kind,
                # Const-rooted alias sources (borrow of self's field in a
                # readonly method) need `const T*` -- classified into
                # const_pointer_alias_locals at the initializing decl.
                const=(kind in (rcfg.FrameLocalKind.PTR_ALIAS,
                                rcfg.FrameLocalKind.OPT_PTR)
                       and lname in const_aliases),
                payload=payload,
                effective_type=effective_type)

        state.frame_layout = rcfg.FrameLayoutPlan(bindings=bindings)
        return state.frame_layout

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
        cfg = self._build_resumable_cfg(func, record_name)
        yields = cfg.yield_sites

        label = f"{record_name}.{func.name}" if record_name else func.name
        kind = "Generator" if self._is_generator_shape() else "Async coroutine"
        out.write(f"// {kind}: {label}\n")
        self._emit_template_header(out, func, record_name=record_name)
        # Async coroutines are awaited, not iterated, so this is "" for them;
        # generator frames gain begin()/end() (see _generator_iter_base).
        base = self._generator_iter_base(func, record_name)
        out.write(f"struct {struct_name}{base} {{\n")

        # State integer (shared) + per-shape extra state (async adds the
        # cancel flag). Frames with abandonment cleanup (a destructor that
        # runs pending finallies / with.__exit__) use ::tpy::frame_state so
        # the defaulted move ctor neuters the source state -- a moved-from
        # frame's destructor must not re-run cleanup.
        dtor_cases = self._dtor_cleanup_cases(cfg)
        if dtor_cases:
            out.write(f"{INDENT}::tpy::frame_state __state;\n")
        else:
            out.write(f"{INDENT}int32_t __state;\n")
        self._emit_resumable_extra_state_fields(out)

        # Captured param fields
        for p in ctor_params:
            out.write(f"{INDENT}{p.field_decl()};\n")

        state = rcfg.resumable_state(func)
        # Hoisted local fields: one field per placement verdict. The
        # classification itself lives in `_frame_layout` (shared with the
        # body-context seeding and THIR admission); this loop only RENDERS
        # each verdict.
        if func.generator_locals:
            layout = self._frame_layout(func)
            for lname, ltype in func.generator_locals:
                ltype_inner = unwrap_ref_type(ltype)
                cpp_name = escape_cpp_name(lname)
                verdict = layout.bindings[lname]
                kind = verdict.kind
                const_pfx = "const " if verdict.const else ""
                if kind is rcfg.FrameLocalKind.PROTOCOL:
                    # No concrete C++ backing for a frame field -- rendering
                    # would emit `frame_slot<Concept>` (ill-formed).
                    raise CodeGenError(
                        f"local {lname!r} of protocol type aliasing a "
                        "protocol-typed parameter is only supported across a "
                        "suspension when bound exactly once directly from the "
                        "parameter; bind it once from the parameter, or "
                        "iterate the parameter directly",
                        loc=func.loc)
                if kind is rcfg.FrameLocalKind.SOURCE_FORM_SLOT:
                    # The payload spelling came from the iteration source at
                    # classification; frame_slot's specializations supply
                    # either the alias or the owning form.
                    out.write(f"{INDENT}::tpy::frame_slot<"
                              f"{verdict.payload}> {cpp_name};\n")
                elif kind is rcfg.FrameLocalKind.OWNED_STR:
                    out.write(f"{INDENT}std::string {cpp_name};\n")
                elif kind is rcfg.FrameLocalKind.PTR_ALIAS:
                    inner_cpp = self.types.type_to_cpp(ltype_inner)
                    out.write(
                        f"{INDENT}{const_pfx}{inner_cpp}* {cpp_name} = nullptr;\n")
                elif kind is rcfg.FrameLocalKind.OWNING_TUPLE_SLOT:
                    storage_cpp = ltype_inner.to_cpp_stored()
                    out.write(f"{INDENT}::tpy::frame_slot<{storage_cpp}> {cpp_name};\n")
                elif kind is rcfg.FrameLocalKind.MIXED_TUPLE_SLOT:
                    out.write(
                        f"{INDENT}::tpy::frame_slot<{verdict.payload}> {cpp_name};\n")
                elif kind is rcfg.FrameLocalKind.BORROW_TUPLE:
                    cpp_type = self.types.tuple_borrow_cpp(ltype_inner)
                    out.write(f"{INDENT}{cpp_type} {cpp_name};\n")
                elif kind is rcfg.FrameLocalKind.VALUE:
                    cpp_type = self.types.type_to_cpp(ltype_inner)
                    out.write(f"{INDENT}{cpp_type} {cpp_name};\n")
                elif kind is rcfg.FrameLocalKind.OPT_PTR:
                    inner_cpp = self.types.type_to_cpp(ltype_inner.inner)
                    out.write(
                        f"{INDENT}{const_pfx}{inner_cpp}* {cpp_name} = nullptr;\n")
                else:
                    cpp_type = self.types.type_to_cpp(ltype_inner)
                    out.write(f"{INDENT}::tpy::frame_slot<{cpp_type}> {cpp_name};\n")

        # Synthetic fields for CFG-decomposed for-loops: one
        # iterator + one __next__-result slot per for-with-await,
        # stored as frame_slot<T> -- same memory shape as the hoisted
        # user-local fields, so the whole frame uses one slot type.
        for fname, ftype in state.for_fields:
            out.write(f"{INDENT}::tpy::frame_slot<{ftype}> {fname};\n")

        # Per-write-site materialization slots for rvalue writes into
        # pointer-form frame locals (see _prescan_resumable_ptr_slots).
        for fname, ftype in state.ptr_slot_fields:
            out.write(f"{INDENT}{ftype} {fname};\n")

        # Context-manager fields for CFG-decomposed `with`-with-await
        # bodies. One per WithItem: an owning `frame_slot<T>` for a
        # by-value manager, or a `T*` borrow for a reference-type lvalue
        # manager (so __enter__/__exit__ act on the original, not a copy).
        for fname, ftype in state.with_fields:
            if fname in state.with_borrowed_fields:
                out.write(f"{INDENT}{ftype}* {fname} = nullptr;\n")
            else:
                out.write(f"{INDENT}::tpy::frame_slot<{ftype}> {fname};\n")

        # In-flight exception slots for CFG-decomposed try-finally-with-
        # await bodies. std::exception_ptr default-constructs
        # to null; the catch arm sets it via std::current_exception().
        # bool pending-return flags need explicit init: NSDMI
        # = false. Value slots default-init via their own type's ctor.
        is_gen = self._is_generator_shape()
        for fname, ftype in state.try_finally_fields:
            # Generators always return StopIteration; no pending-return value
            # slot is needed. The field may be allocated by the prescan when
            # the trial build ran under the async shape -- skip it here.
            if is_gen and fname.startswith("__finally_ret_"):
                continue
            if ftype == "bool":
                out.write(f"{INDENT}{ftype} {fname} = false;\n")
            else:
                out.write(f"{INDENT}{ftype} {fname};\n")

        self._emit_resumable_sub_future_fields(out, func, cfg, record_name)

        # Generators with helper-based finallies that contain a `return` need a
        # stop flag: the helper sets it so `__next__()` emits StopIteration
        # instead of re-throwing (Python: `return` in `finally` suppresses exc).
        if is_gen and any(_stmts_have_return(body)
                          for _, body in cfg.finally_helpers):
            out.write(f"{INDENT}bool __finally_stop = false;\n")

        out.write(f"\n")

        # State enum: S_INITIAL, S_RESUME_<i>, S_JOIN_<n>, S_DONE.
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

        # Constructor. It is a second C++ callee for the same TPy function --
        # an inline `await` and the synthetic async-with / async-for
        # suspensions construct the frame directly instead of going through
        # the factory -- so it carries the same defaults the factory
        # declaration does. `__self` leads and is never defaulted, so the
        # trailing-defaults requirement holds wherever the factory's does.
        self_offset = 1 if record_name else 0
        ctor_param_list = ", ".join(
            p.ctor_param_decl()
            + ("" if i < self_offset else self._default_suffix(func, i - self_offset))
            for i, p in enumerate(ctor_params))
        init_parts = ["__state(S_INITIAL)", *self._resumable_extra_ctor_inits()]
        init_parts.extend(p.ctor_init() for p in ctor_params)
        out.write(f"{INDENT}{struct_name}({ctor_param_list})\n")
        out.write(f"{INDENT}{INDENT}: {', '.join(init_parts)} {{}}\n\n")

        if dtor_cases:
            self._emit_frame_dtor(out, struct_name, dtor_cases)

        # Body-method forward declaration + per-shape extra methods.
        out.write(f"{INDENT}{self._resumable_body_method_fwd_decl(func)};\n")
        self._emit_resumable_extra_methods(out, struct_name)

        # Finally-helper forward declarations: one per TryRegion with a
        # finally body.
        for helper_name, _body in cfg.finally_helpers:
            out.write(f"{INDENT}void {helper_name}();\n")

        # Nested defs become frame member functions (callable from every
        # resume case; frame-field access via implicit this).
        for nd in collect_frame_nested_defs(func.body):
            params_str, nd_ret = emit_prims.nested_def_signature(
                self.types, nd.func)
            ret_str = nd_ret if nd_ret is not None else "void"
            out.write(f"{INDENT}{ret_str} "
                      f"{escape_cpp_name(nd.func.name)}({params_str});\n")

        repr_label = (f"{record_name}.{func.name}" if record_name
                      else func.name)
        repr_kind = "generator" if self._is_generator_shape() else "coroutine"
        param_struct_name = self._struct_name_templated(func, record_name)
        out.write(f"\n{INDENT}friend std::ostream& operator<<("
                  f"std::ostream& os, const {param_struct_name}&) {{\n")
        out.write(f"{INDENT}{INDENT}return os << "
                  f"\"<{repr_kind} {repr_label}>\";\n")
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

        Static-protocol params (`Own[Awaitable[T]]`, `Iterable[T]` etc.)
        take a forwarding-ref `T_<pname>&&`: the factory deduces `T_<pname>`,
        so it may be a value (rvalue arg) OR an lvalue reference (lvalue arg
        -- the common case for a borrowed iterable). `std::forward<T_<pname>>`
        preserves that category; a bare `std::move` would manufacture an
        rvalue that can't bind the ctor's collapsed lvalue ref. `OWNED_VALUE`
        takes its param by value (concrete type, never a deduced reference),
        so it moves. Other kinds pass by bare name.

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
            if cparam.kind in (_CoroParamKind.STATIC_PROTOCOL, _CoroParamKind.FN):
                parts.append(
                    f"std::forward<{cparam.ctor_param_type}>({cparam.cpp_name})")
            elif cparam.kind is _CoroParamKind.OWNED_VALUE:
                parts.append(f"std::move({cparam.cpp_name})")
            else:
                parts.append(cparam.cpp_name)
        return ", ".join(parts)

    # -- poll() body ----------------------------------------------------------

    @contextlib.contextmanager
    def _resumable_frame_ctx(self, func: TpyFunction, record_name: str | None):
        """Set up + tear down resumable-frame ctx state for an async body.

        Routes through the shared `setup_body_scope` primitive so async bodies
        get the same per-scope state setup as sync (reassigned_vars,
        aliased_vars, movable_locals, etc. populated from sema scan).
        Layers the resumable-frame fields (`generator_field_names`,
        `generator_self_ref`, etc.) plus `in_generator_body=True` on top.
        """
        old_in_gen = self.ctx.in_generator_body
        old_field_names = self.ctx.generator_field_names
        old_forwarded_locals = self.ctx.generator_forwarded_locals
        old_optional_fields = self.ctx.generator_optional_fields
        old_frame_slot_locals = self.ctx.generator_frame_slot_locals
        old_borrow_form_loop_vars = self.ctx.generator_borrow_form_loop_vars
        old_for_info = self.ctx.generator_for_loop_info
        old_with_owned_ctx = self.ctx.generator_with_owned_ctx
        old_pointer_alias_locals = self.ctx.generator_pointer_alias_locals
        old_const_pointer_alias_locals = self.ctx.generator_const_pointer_alias_locals
        old_self_ref = self.ctx.generator_self_ref
        old_owned_view_params = self.ctx.owned_view_frame_params
        old_movable_locals = self.ctx.movable_locals
        old_in_method = self.ctx.in_method
        old_method_record = self.ctx.current_method_record_type
        # `setup_body_scope` -> `reset_scope` wipes `pointer_locals` and
        # `current_type_param_bounds` on entry, so the inner body sees a
        # clean state. Save/restore here so a future caller that nests sync
        # body emission around a coro doesn't see inner-coro state leak out
        # on exit.
        old_pointer_locals = self.ctx.pointer_locals
        old_borrow_form_tuple_locals = self.ctx.borrow_form_tuple_locals
        old_optional_borrow_tuple_locals = self.ctx.optional_borrow_tuple_locals
        old_type_param_bounds = self.ctx.current_type_param_bounds
        old_ptr_slot_map = self.ctx.resumable_ptr_slot_map

        # Reset frame-specific fields before setup_body_scope, since the
        # `setup_resumable_frame_locals` call inside it reads
        # `generator_for_loop_info` and writes to `generator_optional_fields`.
        self.ctx.generator_field_names = set()
        self.ctx.generator_optional_fields = set()
        self.ctx.generator_frame_slot_locals = set()
        self.ctx.generator_borrow_form_loop_vars = set()
        # Populated by `_prescan_resumable_for_loops`; carries
        # `pointer_form_loop_var` so `setup_resumable_frame_locals` (called
        # inside `setup_body_scope` below) seeds non-value loop vars into
        # `pointer_locals`. Empty for bodies with no CFG-decomposed for-loop.
        self.ctx.generator_for_loop_info = rcfg.resumable_state(func).for_loop_info
        self.ctx.generator_with_owned_ctx = (
            rcfg.resumable_state(func).with_owned_ctx_map)
        # Seeds pointer_locals inside setup_resumable_frame_locals (called by
        # setup_body_scope below) so borrow-alias frame locals get a `T*` slot.
        self.ctx.generator_pointer_alias_locals = (
            self._classify_pointer_alias_locals(func))
        self.ctx.generator_const_pointer_alias_locals = (
            rcfg.resumable_state(func).const_pointer_alias_locals)

        local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
        for pname, ptype in func.params:
            local_ns.bind_variable(pname, ptype)

        crp, dcbp = self.functions.compute_body_const_sets(func, record_name)
        # Like a sync method: a generator/async method on a generic
        # record needs the record's type-param bounds in scope so for-loop
        # over `self.items: T` resolves T to its protocol bound (otherwise
        # falls back to the universal `::tpy::__iter__` path, missing the
        # direct-iterator / native-iterable peepholes and breaking move-only
        # `Iterator[X]` params).
        record_bounds = None
        if record_name:
            rec_info = self.ctx.analyzer.registry.get_record(record_name)
            if rec_info is not None and rec_info.type_param_bounds:
                record_bounds = rec_info.type_param_bounds
        emit_prims.setup_body_scope(
            self.ctx, self.functions.protocols, self.types,
            func.params, func.return_type, func, local_ns,
            indent_level=1, is_method=bool(record_name),
            const_ref_params=crp, deep_const_borrow_params=dcbp,
            owning_record_name=record_name,
            record_type_param_bounds=record_bounds,
        )

        self.ctx.in_generator_body = True
        self.ctx.owned_view_frame_params = owned_view_frame_params(func.params)
        # Nested defs are frame members: register their names up front so
        # call sites in any resume case (and finally-helper bodies) render
        # the unqualified member call.
        for nd in collect_frame_nested_defs(func.body):
            self.ctx.nested_def_locals.add(nd.func.name)
            self.ctx.local_scope_names.add(nd.func.name)
        # Belt-and-suspenders: `setup_body_scope` registers Own[T] params
        # as movable when `T.is_value_type()` is False, which already
        # covers most static-protocol shapes. Static-protocol frame
        # fields (e.g. `coro: Own[Awaitable[T]]` stored as the deduced
        # `T_coro`) are move-only by construction; force-register them
        # so the call-arg generator emits `std::move(coro)` rather than
        # a copy-into-temp + move, independent of how the protocol's
        # `is_value_type` resolves.
        for cparam in self._classify_params(func, record_name):
            if cparam.kind in (_CoroParamKind.STATIC_PROTOCOL,
                               _CoroParamKind.OWNED_VALUE,
                               _CoroParamKind.FN):
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
        # Forwarded proto-param aliases have no field of their own, but they
        # are still frame-resident names (storage resolves to the backing
        # param) -- register them so the field-name membership tests fire and
        # route through `generator_storage_name`.
        self.ctx.generator_forwarded_locals = dict(func.forwarded_locals or {})
        for lname in self.ctx.generator_forwarded_locals:
            self.ctx.generator_field_names.add(lname)

        try:
            yield
        finally:
            self.ctx.in_generator_body = old_in_gen
            self.ctx.generator_field_names = old_field_names
            self.ctx.generator_forwarded_locals = old_forwarded_locals
            self.ctx.generator_optional_fields = old_optional_fields
            self.ctx.generator_frame_slot_locals = old_frame_slot_locals
            self.ctx.generator_borrow_form_loop_vars = old_borrow_form_loop_vars
            self.ctx.generator_for_loop_info = old_for_info
            self.ctx.generator_with_owned_ctx = old_with_owned_ctx
            self.ctx.generator_pointer_alias_locals = old_pointer_alias_locals
            self.ctx.generator_const_pointer_alias_locals = old_const_pointer_alias_locals
            self.ctx.resumable_ptr_slot_map = old_ptr_slot_map
            self.ctx.generator_self_ref = old_self_ref
            self.ctx.owned_view_frame_params = old_owned_view_params
            self.ctx.movable_locals = old_movable_locals
            self.ctx.in_method = old_in_method
            self.ctx.current_method_record_type = old_method_record
            self.ctx.pointer_locals = old_pointer_locals
            self.ctx.borrow_form_tuple_locals = old_borrow_form_tuple_locals
            self.ctx.optional_borrow_tuple_locals = old_optional_borrow_tuple_locals
            self.ctx.current_type_param_bounds = old_type_param_bounds

    def gen_coro_finally_top_def(self, out: "TextIO", func: TpyFunction,
                                   record_name: str | None = None) -> None:
        """Emit member-function bodies the frame declares beyond its body
        method: every `__finally_<n>()` helper the CFG produced (one per
        TryRegion with a finally body), and every nested def (emitted as a
        frame member so it is callable from any resume case and reaches
        frame-field locals via implicit this). No-op when neither exists.
        """
        cfg = self._build_resumable_cfg(func, record_name)
        nested_defs = collect_frame_nested_defs(func.body)
        if not cfg.finally_helpers and not nested_defs:
            return
        struct_name = self._struct_name_templated(func, record_name)
        # Helper-finally statements come from the same lowering attempt as the
        # body (the second lookup is cached), and render through the installed
        # leaf emitter like any other BB leaf.
        leaf = self._thir_resumable_leaf_emitter(func, record_name, cfg)

        with self._resumable_frame_ctx(func, record_name):
            with self._thir_leaf_scope(leaf):
                for helper_name, body_stmts in cfg.finally_helpers:
                    self._emit_template_header(
                        out, func, record_name=record_name)
                    out.write(f"void {struct_name}::{helper_name}() {{\n")
                    self.ctx.indent_level = 1
                    with self._generator_finally_helper_scope():
                        for stmt in body_stmts:
                            leaf.emit_leaf_stmt(
                                out, stmt, self.ctx.indent_level)
                    self.ctx.indent_level = 0
                    out.write(f"}}\n")
            for nd in nested_defs:
                params_str, nd_ret = emit_prims.nested_def_signature(
                    self.types, nd.func)
                ret_str = nd_ret if nd_ret is not None else "void"
                self._emit_template_header(out, func, record_name=record_name)
                out.write(f"{ret_str} {struct_name}::"
                          f"{escape_cpp_name(nd.func.name)}({params_str}) {{\n")
                self.ctx.indent_level = 1
                # The member body was lowered under the member scope at
                # frame lowering; a missing entry is a lowering/seam
                # disagreement.
                leaf.emit_nested_def_body(out, nd.func, self.ctx.indent_level)
                self.ctx.indent_level = 0
                out.write(f"}}\n")

    # =====================================================================
    # Resumable-shape policy seam. These methods isolate the decisions
    # that differ between the async (`await` -> `__poll__`) shape and the
    # generator (`yield` -> `__next__`) shape, so one state-machine emitter
    # serves both. Each seam branches on `self._shape`; async never enters
    # the generator branch.
    # =====================================================================

    def _generator_slot_cpp(self, func: TpyFunction) -> str:
        """The generator's __next__ iterator slot type -- the element type that
        is BOTH the std::expected payload (`_resumable_ret_type_cpp`) and the
        next_iter_mixin element arg (`_generator_iter_base`); the two must stay
        identical, so they share this single source."""
        yt = func.generator_yield_type
        return GeneratorCodegen._iter_slot_for_yield(yt, self.types.type_to_cpp(yt))

    def _generator_iter_base(self, func: TpyFunction,
                             record_name: str | None = None) -> str:
        # The struct name must be templated here: a base-specifier predates the
        # injected-class-name, so a generic frame needs its explicit `<...>`.
        if not self._is_generator_shape():
            return ""
        struct_templated = self._struct_name_templated(func, record_name)
        return (f" : public ::tpy::next_iter_mixin"
                f"<{struct_templated}, {self._generator_slot_cpp(func)}>")

    def _resumable_ret_type_cpp(self, func: TpyFunction) -> str:
        """The frame body method's return type. Async: `Poll<T>`.
        Generator: `std::expected<T_slot, ::tpy::StopIteration>` where
        T_slot is the iterator slot type from
        `gen_generators._iter_slot_for_yield` -- borrow form
        (`std::tuple<T&, ...>` / `std::tuple<P*, ...>`) for tuple yields,
        bare cpp type otherwise. The yield emit already bridges
        storage-form sources into the borrow-form slot when
        `ctx.current_yield_type` is set (done by
        `_resumable_return_lowering`)."""
        if self._is_generator_shape():
            return f"std::expected<{self._generator_slot_cpp(func)}, ::tpy::StopIteration>"
        return self._poll_ret_cpp(func)

    def _resumable_body_method_decl(self, func: TpyFunction,
                                    struct_name: str) -> str:
        """Full declarator of the frame's body method: return type +
        qualified name + params. Async: `Poll<T> <struct>::__poll__(Waker
        waker)`. Generator: `expected<T, StopIteration> <struct>::__next__()`
        (no waker)."""
        if self._is_generator_shape():
            return (f"{self._resumable_ret_type_cpp(func)} "
                    f"{struct_name}::__next__()")
        return (f"{self._poll_ret_cpp(func)} {struct_name}::__poll__("
                f"::tpystd::coro::Waker waker)")

    def _emit_resumable_body_prelude(self, out: "TextIO",
                                     has_suspensions: bool) -> None:
        """Per-shape body prelude. Async silences the unused `waker`
        param when the frame has no suspensions; the generator shape has
        no waker param, so it emits nothing."""
        if self._is_generator_shape():
            return
        if not has_suspensions:
            out.write(f"{INDENT}(void)waker;\n")

    def _emit_resumable_done_case(self, out: "TextIO", inner: str) -> None:
        """The terminal `S_DONE` switch case. Async panics on a re-poll
        after Ready; the generator returns `StopIteration` (a repeat
        `__next__()` after exhaustion is well-defined in Python)."""
        if self._is_generator_shape():
            out.write(f"{inner}case S_DONE: return "
                      f"::tpy::make_unexpected(::tpy::StopIteration{{}});\n")
            return
        out.write(f"{inner}case S_DONE: "
                  f"::tpy::tpy_panic(\"poll after Ready\");\n")

    @contextlib.contextmanager
    def _resumable_return_lowering(self, func: TpyFunction):
        """Configure how a source-level `return` lowers inside the frame
        body, then restore (policy seam). Async: the statement emitter
        consults `ctx.in_async_coro_body` to rewrite `return v` into
        `__state = S_DONE; return Poll::ready(v)`. The generator shape
        lowers a bare `return` to the StopIteration/done path and rejects
        return-with-value."""
        if self._is_generator_shape():
            old_in_gen = self.ctx.in_generator_resumable_body
            old_done = self.ctx.generator_resumable_done_state
            old_yt = self.ctx.current_yield_type
            self.ctx.in_generator_resumable_body = True
            self.ctx.generator_resumable_done_state = "S_DONE"
            self.ctx.current_yield_type = func.generator_yield_type
            try:
                yield
            finally:
                self.ctx.in_generator_resumable_body = old_in_gen
                self.ctx.generator_resumable_done_state = old_done
                self.ctx.current_yield_type = old_yt
            return
        old_in_async = getattr(self.ctx, "in_async_coro_body", False)
        old_async_ret_cpp = getattr(self.ctx, "async_coro_return_cpp", None)
        old_async_done_label = getattr(self.ctx, "async_coro_done_state", None)
        self.ctx.in_async_coro_body = True
        self.ctx.async_coro_return_cpp = self._ret_cpp(func)
        self.ctx.async_coro_done_state = "S_DONE"
        self.ctx.current_return_type = func.return_type
        try:
            yield
        finally:
            self.ctx.in_async_coro_body = old_in_async
            self.ctx.async_coro_return_cpp = old_async_ret_cpp
            self.ctx.async_coro_done_state = old_async_done_label

    @contextlib.contextmanager
    def _generator_finally_helper_scope(self):
        """While emitting a `__finally_<n>()` helper body of a generator,
        `ctx.in_generator_finally_helper` makes `return` lower to
        `this->__finally_stop = true; return;` (void) instead of `goto __done`.
        No-op for the async shape (helpers there return Poll)."""
        old_in_helper = self.ctx.in_generator_finally_helper
        if self._is_generator_shape():
            self.ctx.in_generator_finally_helper = True
        try:
            yield
        finally:
            self.ctx.in_generator_finally_helper = old_in_helper

    @contextlib.contextmanager
    def _generator_finally_stop_scope(self, has_finally_stop: bool):
        """While emitting a generator state-machine body, expose whether any
        finally helper contains a `return` (so the helper call sites append the
        `if (this->__finally_stop) return StopIteration;` check), then restore."""
        old_has_finally_stop = self.ctx.generator_has_finally_stop
        self.ctx.generator_has_finally_stop = has_finally_stop
        try:
            yield
        finally:
            self.ctx.generator_has_finally_stop = old_has_finally_stop

    def _emit_resumable_extra_state_fields(self, out: "TextIO") -> None:
        """Per-shape state fields beyond the shared `int32_t __state`.
        Async adds the cancellation flag; the generator shape adds
        nothing."""
        if self._is_generator_shape():
            return
        out.write(f"{INDENT}bool __cancel_pending;\n")

    def _resumable_extra_ctor_inits(self) -> list[str]:
        """Per-shape ctor member-init entries beyond `__state(S_INITIAL)`.
        Async initializes the cancel flag; the generator shape adds none.
        """
        if self._is_generator_shape():
            return []
        return ["__cancel_pending(false)"]

    def _protocol_template_args_by_suspension(
            self, func: TpyFunction, record_name: str | None,
            cfg: 'rcfg.CFG') -> 'dict[int, list[str]]':
        """The `T_<pname>` template args each suspension's sub-coro struct
        name needs, keyed by suspension index.

        Rendered here rather than where the callee is resolved, because the
        spelling must equal what the emplace hands the ctor and the emplace
        coercions are only decidable under the frame body scope: the `self`
        -> `__self` rewrite and the frame-slot deref come from that scope,
        and so do the narrowing facts the coercions read. Entering it is also
        what keeps `frame_field_shadows` this body's, rather than whatever the
        previously emitted body left behind.

        The working movable set is seeded from the sema fact because no
        statement has run yet: the emplace sees every frame local already
        promoted by its declaration, while this render sees none, and an owned
        argument would then render as a copy into a temp whose name does not
        exist at class scope. The deduced capture type is the same either way
        -- an `Own`-shaped param renders an rvalue whether it moves the source
        or a materialized copy of it -- so the seed decides only whether the
        spelling names a temp, never what the field's type comes out as.

        A spelling that disagrees with the emplace fails at the C++ bind:
        the sub-coro ctor takes `T_<pname>&&`, so a value-category flip does
        not compile. The exception is a `T` deduced as a const reference,
        which binds an rvalue and would capture a dangling one -- the seed
        only ever widens toward moving, never toward borrowing, which is
        what keeps that shape out of reach rather than any check here.

        Nothing is published from here: the render is discarded, so it drives
        no emitted move. The body walk renders the authoritative spelling when
        it emits the same argument at the emplace.

        The leaf emitter is installed for the same reason the body scope is:
        a routed body renders its emplace arguments off its own lowered
        nodes, so the capture type has to be read from the same author or the
        two spellings can only agree by accident.
        """
        pending = [y for y in cfg.yield_sites
                   if y.payload.prebuilt_slot is None
                   and self._protocol_param_positions(y.payload.operand_expr)]
        if not pending:
            return {}
        leaf = self._thir_resumable_leaf_emitter(func, record_name, cfg)
        args: dict[int, list[str]] = {}
        with self._resumable_frame_ctx(func, record_name):
            self.ctx.movable_locals |= self.ctx.sema_movable_locals
            with self._thir_leaf_scope(leaf):
                for y in pending:
                    extra = self._extra_template_args_for_await(
                        y.payload.operand_expr)
                    if extra:
                        args[y.suspension_index] = extra
        return args

    def _emit_resumable_sub_future_fields(
            self, out: "TextIO", func: TpyFunction, cfg: 'rcfg.CFG',
            record_name: str | None = None) -> None:
        """Sub-future frame fields -- async-only (policy seam). One per
        Yield (suspension_index = field ordinal): Inline ->
        optional<sub-coro struct>, Erased -> optional<value awaitable>,
        Borrowed -> raw pointer. Async-with's synthetic yields override
        sub_field_cpp_type via the state.async_with_struct_names map (the CM's
        __aenter__/__aexit__ coro struct, computed at prescan time). The
        generator shape stores nothing at a `yield`, so it emits nothing.
        """
        if self._is_generator_shape():
            return
        state = rcfg.resumable_state(func)
        struct_names = state.async_with_struct_names
        for_struct_names = state.async_for_struct_names
        protocol_args = self._protocol_template_args_by_suspension(
            func, record_name, cfg)
        for y in cfg.yield_sites:
            p = y.payload
            if p.prebuilt_slot is not None:
                # Bound-coroutine await: polls the handle's own frame
                # field; no dedicated sub-future slot.
                continue
            sub_cpp = p.sub_field_cpp_type
            extra = protocol_args.get(y.suspension_index)
            if extra:
                sub_cpp = self._sub_struct_qualname(
                    p.await_node.awaited_method_owner_type,
                    p.await_node.awaited_async_func_name,
                    getattr(p.await_node.value, "inferred_type_args", None),
                    module_qual=p.sub_struct_module_qual,
                    extra_template_args=extra,
                    loc=p.await_node.loc)
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

    def _resumable_body_method_fwd_decl(self, func: TpyFunction) -> str:
        """In-struct forward declaration of the body method (unqualified).
        Async: `Poll<T> __poll__(Waker waker)`. Generator:
        `expected<T, StopIteration> __next__()`."""
        if self._is_generator_shape():
            return f"{self._resumable_ret_type_cpp(func)} __next__()"
        return (f"{self._poll_ret_cpp(func)} "
                f"__poll__(::tpystd::coro::Waker waker)")

    def _emit_resumable_extra_methods(self, out: "TextIO",
                                      struct_name: str) -> None:
        """Per-shape inline methods on the frame struct. Async emits
        `cancel()`; the generator emits `__iter__()` (returns `*this`, so
        the frame is its own iterator -- parallels gen_generators)."""
        if self._is_generator_shape():
            out.write(f"{INDENT}{struct_name}& __iter__() "
                      f"{{ return *this; }}\n")
            return
        out.write(f"{INDENT}void cancel() {{ __cancel_pending = true; }}\n")

    def gen_coro_poll_def(self, out: "TextIO", func: TpyFunction,
                            record_name: str | None = None) -> None:
        """Emit the `poll()` method body in the .cpp file (or inline-in-hpp
        for templates -- the caller handles placement). When `record_name`
        is given, the struct is `__coro_<Record>_<func>` and the body
        sees `self.X` as `__self.X` (parallels generator methods).
        """
        struct_name = self._struct_name_templated(func, record_name)
        cfg = self._build_resumable_cfg(func, record_name)
        has_yields = bool(cfg.yield_sites)
        leaf = self._thir_resumable_leaf_emitter(func, record_name, cfg)

        self.ctx.emit_source_comment(out, func.loc)
        self._emit_template_header(out, func, record_name=record_name)
        out.write(f"{self._resumable_body_method_decl(func, struct_name)} {{\n")
        self._emit_resumable_body_prelude(out, has_yields)

        # Return-rewrite setup (the C++ Poll<T> type and DONE state
        # label so `return v` lowers correctly inside the state machine)
        # is the resumable-shape policy's responsibility.
        has_finally_stop = (
            self._is_generator_shape()
            and any(_stmts_have_return(body) for _, body in cfg.finally_helpers))
        with self._generator_finally_stop_scope(has_finally_stop):
            with self._resumable_frame_ctx(func, record_name):
                with self._resumable_return_lowering(func):
                    with self._thir_leaf_scope(leaf):
                        self._emit_state_machine(out, func, cfg)

        out.write(f"}}\n")

    # -- THIR resumable seam ----------------------------------------------
    # The state-machine skeleton (this module + resumable_cfg) is SHARED
    # machinery, like signatures and struct layout; only the user-source
    # LEAVES route through THIR. A routed body swaps every leaf-delegation
    # site (BB leaf stmts, RaiseT stmts, Branch conds, emplace args, the
    # async-return value in _make_async_return) to the leaf emitter below;
    # per-body routing stays all-or-nothing.

    def _thir_resumable_attempt(self, func: TpyFunction,
                                record_name: str | None,
                                cfg: 'rcfg.CFG'):
        """Attempt-once THIR leaf lowering for this frame body, cached so a
        re-entered frame lowers exactly once."""
        cache = self.ctx.thir_resumables
        if func in cache:
            return cache[func]
        from ..thir.reject import (begin_attempt, commit_attempt,
                                   reject_attempt)
        from ..thir.lower.resumable import lower_resumable
        begin_attempt()
        # The case-label set (cached on the CFG) doubles as the lowering's
        # narrowing-alias boundary: case entries re-establish `__{var}`.
        case_entry_ids = frozenset(self._compute_case_entries(cfg))
        # The frame-layout plan is the skeleton's own placement decision;
        # handing it to lowering (vs re-deriving) keeps the skeleton and the
        # lowered leaves on one frame-field form decision.
        rb = lower_resumable(func, self.ctx.analyzer, self.types.type_to_cpp,
                             cfg, record_name=record_name,
                             render_type_stored=self.types.type_to_cpp_stored,
                             case_entry_ids=case_entry_ids,
                             native_globals=self.ctx.native_global_names,
                             frame_layout=self._frame_layout(func))
        cache[func] = rb
        if rb is None:
            reject_attempt("resumable", func)
        commit_attempt()
        return rb

    def _thir_resumable_leaf_emitter(self, func: TpyFunction,
                                     record_name: str | None,
                                     cfg: 'rcfg.CFG'):
        """The body's leaf renderer bound to the live ctx sinks."""
        rb = self._thir_resumable_attempt(func, record_name, cfg)
        from ..thir.emit import (CommentSink, ModuleCounter, CtxIterCounter,
                                 TempSink, ResumableLeafEmitter)

        def _return_hook(stmt: TpyReturn, indent_level: int) -> str:
            # Nested leaf return: the same scaffolding a ReturnT terminator
            # gets. The value render inside re-enters the leaf seam's
            # return_values table via _async_return_value_cpp.
            return self._resumable_return_code(stmt, INDENT * indent_level)

        return ResumableLeafEmitter(
            rb,
            comments=CommentSink(self.ctx),
            temps=TempSink(self.ctx),
            with_counter=ModuleCounter(self.ctx, "with_counter"),
            try_counter=ModuleCounter(self.ctx, "try_except_counter"),
            finally_guard_counter=ModuleCounter(
                self.ctx, "finally_guard_counter"),
            # The skeleton registers loop-var shadows of frame fields in the
            # LIVE ctx set; leaf renders must suppress the frame `(*name)`
            # deref exactly while a shadow is in scope.
            frame_shadow_probe=(
                lambda n: n in self.ctx.frame_field_shadows),
            resumable_return_hook=_return_hook,
            # The leaf finally bridge: THIR finally frames mirror onto the
            # ctx finally stack (so _make_async_return's chain walk inlines
            # them) with one shared guard-liveness truth.
            live_finally_guards=self.ctx.live_finally_guards,
            ast_finally_push=partial(emit_prims.push_finally, self.ctx),
            ast_finally_pop=lambda: self.ctx.finally_stack.pop(),
            iter_counter=CtxIterCounter(self.ctx))

    @property
    def _leaf(self) -> "ResumableLeafEmitter":
        """The live frame's leaf renderer. Every user-source render inside a
        frame body goes through it, so a missing one is a seam bug rather
        than a shape this emitter can render itself."""
        leaf = self.ctx.thir_resumable_leaf
        if leaf is None:
            raise CodeGenError(
                "internal error: no resumable leaf emitter installed", None)
        return leaf

    @contextlib.contextmanager
    def _thir_leaf_scope(self, leaf):
        old = self.ctx.thir_resumable_leaf
        self.ctx.thir_resumable_leaf = leaf
        try:
            yield
        finally:
            self.ctx.thir_resumable_leaf = old

    # =====================================================================
    # `return` inside a resumable frame body (shape-dispatched scaffolding).
    # =====================================================================

    def _resumable_return_code(self, stmt: TpyReturn, indent: str) -> str:
        """Lower a source-level `return` for the frame shape currently being
        emitted -- the flags `_resumable_return_lowering` and the generator
        finally-helper scope set around the whole body emission."""
        if self.ctx.in_async_coro_body:
            return self._make_async_return(stmt, indent)
        if self.ctx.in_generator_resumable_body:
            # Generator on the resumable frame: bare return / end ->
            # StopIteration done, via the while/switch (no __done label).
            return self._make_generator_resumable_return(stmt, indent)
        if self.ctx.in_generator_finally_helper:
            # return inside a helper-based finally body: set the stop flag
            # and void-return; __next__() checks __finally_stop after the
            # helper call and emits StopIteration (Python: return in finally
            # suppresses any pending exception).
            return (f"{indent}this->__finally_stop = true;\n"
                    f"{indent}return;\n")
        raise CodeGenError(
            "internal: resumable return outside a resumable body emission")

    def _async_return_value_cpp(self, stmt: TpyReturn, ret_type,
                                *, to_borrow: bool,
                                allow_move: bool = False) -> str:
        """The value render for `_make_async_return`'s three scaffolding
        sites (pending-slot store / pre-finally capture / direct ready) --
        and the resumable THIR seam's return-value chokepoint: a routed
        body renders the value from its lowered node, the scaffolding
        around it is shared skeleton either way. `allow_move` is True only
        at the direct-ready site: the pre-finally sites must copy, because
        an alias bound before the try can still read the local from the
        finally body (liveness's alias tracking does not survive the
        return arm, so the last-use fact alone cannot rule that out)."""
        # The render is position-blind: it replaces only the value string,
        # and every view/borrow lift the scaffolding would otherwise apply is
        # covered at lowering for each admitted return shape. Serves ReturnT
        # terminators AND nested leaf returns (THIRResumableReturn's emit hook
        # re-enters _make_async_return, which lands back here). Widening the
        # return-shape gate must revisit this seam -- see the
        # THIRResumableBody return_values contract.
        return self._leaf.render_return_value(stmt, allow_move=allow_move)

    def _make_async_return(self, stmt: TpyReturn, indent: str) -> str:
        """Lower `return v` inside an `async def` body. When a CFG-based
        finally is active, ctx state routes the return through the
        pending-return slot: save value + flag, walk finally frames
        inside the finally's body (above the boundary), transition to
        the finally entry. AsyncFinallyExit emits the actual Poll::ready
        at the finally tail. Otherwise emit Poll::ready directly after
        walking the finally chain."""
        ret_type = unwrap_ref_type(self.ctx.current_return_type)
        done_state = self.ctx.async_coro_done_state or "S_DONE"
        out = io.StringIO()
        pending_flag = self.ctx.async_pending_return_flag
        if pending_flag is not None:
            pending_slot = self.ctx.async_pending_return_slot
            target_state = self.ctx.async_pending_return_target_state
            boundary = self.ctx.async_pending_return_boundary
            assert target_state is not None
            if pending_slot is not None and stmt.value is not None:
                # The pending slot is typed as the Poll payload (`ret_cpp`),
                # so a borrow-form return stores its pointer here -- alias-
                # correct through the suspending finally. For STORAGE
                # payloads this remains the KNOWN-WRONG eager COPY: a
                # mutation of the returned local by the suspending finally
                # is invisible in the returned object (CPython's pending
                # return aliases), and a @nocopy payload fails to build. A
                # move is NOT the fix (an alias in the finally would read a
                # gutted object); the deferral needs a parked discriminant
                # at AsyncFinallyExit -- tracked in BUGS.md.
                expr_cpp = self._async_return_value_cpp(stmt, ret_type,
                                                        to_borrow=True)
                out.write(f"{indent}this->{pending_slot} = {expr_cpp};\n")
            out.write(f"{indent}this->{pending_flag} = true;\n")
            # Walk finally frames pushed by regions INSIDE the CFG-
            # based finally (above the boundary). Frames pushed by
            # regions outside run later in AsyncFinallyExit.
            terminated = emit_prims.emit_finally_chain(self.ctx, out, indent,
                                                       stop_at=boundary)
            if not terminated:
                out.write(f"{indent}__state = {target_state};\n")
                out.write(f"{indent}continue;\n")
            return out.getvalue()
        # Walk enclosing finally chain (try/with around an `await` or just a
        # return inside try/finally). Same machinery as sync _make_return:
        # the return value must be captured BEFORE the chain runs (Python
        # evaluates the return expression first, then finally bodies).
        ret_tmp: str | None = None
        deferred_materialize: str | None = None
        ret_cpp = self.ctx.async_coro_return_cpp or "void"
        if (not isinstance(ret_type, VoidType) and stmt.value is not None
                and self.ctx.finally_stack):
            recipe = None
            if stmt.finally_deferred_capture:
                # A stamped return ALWAYS has a recipe: lowering rejects the
                # whole body when it cannot build one.
                recipe = self._leaf.render_deferred_return(stmt)
            if recipe is not None:
                ptr, capture_rhs, deferred_materialize = recipe
                chain = io.StringIO()
                terminated = emit_prims.emit_finally_chain(self.ctx, chain,
                                                           indent)
                maybe_unused = "[[maybe_unused]] " if terminated else ""
                out.write(f"{indent}{maybe_unused}auto* {ptr} = {capture_rhs};\n")
                out.write(chain.getvalue())
            else:
                expr_cpp = self._async_return_value_cpp(stmt, ret_type,
                                                        to_borrow=True)
                ret_tmp = f"__tpy_async_ret_{self.ctx.iter_counter}"
                self.ctx.iter_counter += 1
                chain = io.StringIO()
                terminated = emit_prims.emit_finally_chain(self.ctx, chain,
                                                           indent)
                maybe_unused = "[[maybe_unused]] " if terminated else ""
                # For deferral-INELIGIBLE reference shapes (declared unions,
                # tuples, ...) this eager capture is a KNOWN-WRONG pre-chain
                # COPY: a finally mutation of the local is invisible in the
                # returned object (CPython's pending return aliases) --
                # tracked in BUGS.md; a move here would be worse (the
                # finally can still read the local through an alias).
                out.write(f"{indent}{maybe_unused}{ret_cpp} {ret_tmp} = {expr_cpp};\n")
                out.write(chain.getvalue())
        else:
            terminated = emit_prims.emit_finally_chain(self.ctx, out, indent)
        if terminated:
            return out.getvalue()
        out.write(f"{indent}__state = {done_state};\n")
        if isinstance(ret_type, VoidType):
            out.write(f"{indent}{POLL_VOID_READY_RETURN}\n")
        else:
            if stmt.value is None:
                # Non-void async def with bare return -- sema should have
                # caught this; emit a panic as a guardrail.
                out.write(
                    f"{indent}::tpy::tpy_panic(\"non-void async def used bare return\");\n")
            else:
                if deferred_materialize is not None:
                    out.write(
                        f"{indent}return ::tpystd::tpy::Poll<{ret_cpp}>::ready("
                        f"{deferred_materialize});\n")
                    return out.getvalue()
                if ret_tmp is None:
                    # Direct ready: no finally chain follows, so this is the
                    # one site where a last-use move is unconditionally safe.
                    expr_cpp = self._async_return_value_cpp(stmt, ret_type,
                                                            to_borrow=True,
                                                            allow_move=True)
                    # Bind to a local first so `std::move` has a typed source:
                    # `std::move({1, 2, 3})` (braced initializer) doesn't
                    # compile because the template parameter can't be deduced.
                    ret_tmp = "__tpy_async_ret"
                    out.write(f"{indent}{ret_cpp} {ret_tmp} = {expr_cpp};\n")
                out.write(
                    f"{indent}return ::tpystd::tpy::Poll<{ret_cpp}>::ready("
                    f"std::move({ret_tmp}));\n")
        return out.getvalue()

    def _make_generator_resumable_return(self, stmt: TpyReturn,
                                         indent: str) -> str:
        """Lower a `return` inside a generator body lowered onto the
        resumable frame. Generators reject return-with-value (sema), so
        this is always a bare `return` meaning "stop iteration": walk the
        enclosing finally chain, then (if not already terminated) set the
        done state and return StopIteration. Parallels _make_async_return
        but with the generator's `expected<T, StopIteration>` done shape.

        When a CFG-based finally is active (ctx.async_pending_return_flag),
        the return is deferred: set the pending flag, walk finallies inside
        the boundary, transition the state machine to the finally entry.
        The finally tail will emit StopIteration once it completes."""
        out = io.StringIO()
        pending_flag = self.ctx.async_pending_return_flag
        if pending_flag is not None:
            # CFG-based finally (yield-in-finally): defer the StopIteration.
            target_state = self.ctx.async_pending_return_target_state
            boundary = self.ctx.async_pending_return_boundary
            assert target_state is not None
            # No return-value slot for generators (always StopIteration).
            out.write(f"{indent}this->{pending_flag} = true;\n")
            terminated = emit_prims.emit_finally_chain(self.ctx, out, indent,
                                                       stop_at=boundary)
            if not terminated:
                out.write(f"{indent}__state = {target_state};\n")
                out.write(f"{indent}continue;\n")
            return out.getvalue()
        terminated = emit_prims.emit_finally_chain(self.ctx, out, indent)
        if terminated:
            return out.getvalue()
        done_state = self.ctx.generator_resumable_done_state or "S_DONE"
        out.write(f"{indent}__state = {done_state};\n")
        out.write(f"{indent}return ::tpy::make_unexpected("
                  f"::tpy::StopIteration{{}});\n")
        return out.getvalue()

    # =====================================================================
    # CFG-based state-machine emitter (replaces _emit_switch_body).
    # =====================================================================

    def _build_resumable_cfg(self, func: TpyFunction,
                             record_name: str | None = None) -> 'rcfg.CFG':
        """Apply the await-lift pre-pass, then build the CFG. The CFG
        builder handles any wrapping try/finally uniformly with all
        other compound statements -- no special unwrap-and-rewrap pass
        is needed.

        A `_CFGNotYetSupported` (a shape the resumable lowering can't yet
        handle) is turned into a `CodeGenError` at the offending location.
        This is the authoritative backstop so no unsupported shape silently
        miscompiles. The result is cached on the func for the emit pass.

        `record_name` (the enclosing coro's record, for a method) lets the
        sub-future field-type computation render await-arg expressions with
        the same body name-context the emplace uses (`self`->`__self`,
        `frame_slot` deref) -- otherwise the field-type and emplace spellings
        diverge. Generators never await, so the gate path may pass None.
        """
        state = rcfg.resumable_state(func)
        if state.cfg is not None:
            return state.cfg
        body = self._effective_body(func)
        # The for-loop prescan resolves each loop's iterable type via the
        # name-keyed `var_types`, which on entry still holds the
        # previously-emitted function's locals/params -- so a second generator
        # whose param shares a name inherits the stale type (e.g. a protocol
        # param's `Iterable[T]` leaking onto a concrete `list` param of the
        # same name). Seed `var_types` with THIS function's own params for the
        # prescan window; `_resumable_frame_ctx` resets scope again for build.
        saved_var_types = self.ctx.var_types
        self.ctx.var_types = {pname: unwrap_ref_type(ptype)
                              for pname, ptype in func.params}
        # The strategy analysis also consults the const sets (a const-rooted
        # iterable needs a const_iterator frame slot), which on entry still
        # hold the previous function's values -- seed FRAME-CAPTURE constness,
        # not the sync signature inference: the frame stores `const Self&`
        # for a readonly method and `const T&` for explicit readonly[T]
        # params, but captures inferred-const REFERENCE params as mutable
        # `T&`, so sync const sets would over-mark param-rooted sources.
        # Pointer-repr tuple/union captures are the exception: their frame
        # fields spell the inferred verdict (`_classify_params`), so the
        # window must agree or alias locals type against the wrong field.
        saved_crp = self.ctx.const_ref_params
        saved_dcbp = self.ctx.deep_const_borrow_params
        crp: set[str] = set()
        dcbp: set[str] = set()
        if func.is_readonly and (record_name is not None or func.is_method):
            crp.add("self")
        _fdc = self._frame_deep_const_verdict(func, record_name)
        for _pidx, (pname, ptype) in enumerate(func.params):
            if isinstance(unwrap_ref_type(ptype), ReadonlyType):
                crp.add(pname)
                dcbp.add(pname)
                continue
            bare = unwrap_readonly(unwrap_ref_type(ptype))
            if (_fdc is not None and _pidx in _fdc
                    and ((isinstance(bare, TupleType)
                          and bare.has_pointer_repr_element())
                         or self.ctx.is_ptr_variant_union(bare))):
                dcbp.add(pname)
        self.ctx.const_ref_params = crp
        self.ctx.deep_const_borrow_params = dcbp
        # Classify borrow-alias locals under the seeded const sets (the
        # classification is memoized, so it must not first run against a
        # stale context) and expose the const subset: the strategy analysis
        # types const-alias iterables' frame iterator slots off it.
        saved_alias = self.ctx.generator_pointer_alias_locals
        saved_alias_const = self.ctx.generator_const_pointer_alias_locals
        self.ctx.generator_pointer_alias_locals = (
            self._classify_pointer_alias_locals(func))
        self.ctx.generator_const_pointer_alias_locals = (
            rcfg.resumable_state(func).const_pointer_alias_locals)
        try:
            for_uid_map = self._prescan_resumable_for_loops(func, body)
            with_uid_map = self._prescan_with_stmts(func, body)
            # Both of these build the frame layout, so they must follow the
            # for/with prescans whose outputs it reads (for_loop_info,
            # with_owning_str_targets, with_target_payloads). Neither's own
            # output feeds the layout, so building it here is not circular.
            self._prescan_with_manager_homes(func, body)
            self._prescan_resumable_ptr_slots(func, body)
            try_finally_uid_map = self._prescan_resumable_try_finally(func, body)
            builder = rcfg.CFGBuilder(
                payload_factory=self._make_await_payload,
                for_uid_map=for_uid_map,
                with_uid_map=with_uid_map,
                try_finally_uid_map=try_finally_uid_map,
                func_returns_void=self._is_void_return(func),
            )
            # Build inside the resumable-frame body context so the payload
            # factory's field-type render (`_extra_template_args_for_await`)
            # applies the `self`->`__self` rewrite and `frame_slot` deref,
            # matching the emplace site. `setup_body_scope` is side-effect-free
            # state setup, so entering it here (build) and again at emit is
            # safe; the CFG is cached, so this runs once.
            with self._resumable_frame_ctx(func, record_name):
                cfg = builder.build(body)
        except rcfg._CFGNotYetSupported as e:
            raise CodeGenError(e.msg, loc=e.loc)
        finally:
            self.ctx.var_types = saved_var_types
            self.ctx.const_ref_params = saved_crp
            self.ctx.deep_const_borrow_params = saved_dcbp
            self.ctx.generator_pointer_alias_locals = saved_alias
            self.ctx.generator_const_pointer_alias_locals = saved_alias_const
        # Stash the builder so callers (emit) can look up handler
        # entries via builder.get_handler_entry().
        state.cfg_builder = builder
        # Inline awaits resolve their callee in the payload factory during the
        # build, so their edges ride the payload; the `async with` / `async for`
        # and `iter_next` embeddings are recorded by the prescans above. Between
        # them the frame's by-value embeddings are covered without re-walking
        # the AST, which cannot see the `async with`/`async for` sub-futures at
        # all -- they carry no TpyAwait node.
        for _y in cfg.yield_sites:
            _dep = getattr(_y.payload, "dep_unit", None)
            if _dep is not None:
                state.frame_dep_units.append(_dep)
        state.cfg = cfg
        return cfg

    def _prescan_resumable_for_loops(
            self, func: TpyFunction,
            body: list[TpyStmt]) -> dict[int, int]:
        """Find for-loops whose body contains await OR are `async for`
        (M6). For each, allocate a uid, register frame-field declarations
        and register the loop variable as a hoisted local so it persists
        across suspensions.

        Sync for-with-await (M3.1): two slots,
        `(__for_itr_N: iter_type_t<S>, __for_r_N: iter_result_t<S>)`.

        Async-for (M6): one slot, `__for_itr_N: aiter_type_t<S>`. No `__for_r_N` slot --
        the advance is a Yield(await __anext__()) and the unwrapped
        value goes straight to the loop var via the standard resume-bind
        path. Also populates `state.async_for_struct_names[uid]` with
        the C++ name of the `__anext__` sub-coro struct so the Yield
        emit can size `__sub_<i>` and emplace it.

        Returns {id(TpyForEach) -> uid} for the CFG builder. Frame fields
        live on `state.for_fields` as `[(name, cpp_type)]` consumed
        by gen_coro_struct (see `ResumableFuncState`).
        """
        state = rcfg.resumable_state(func)
        if state.for_prescanned:
            return state.for_uid_map
        # Static-protocol params are captured as a deduced template arg
        # `T_<pname>` (not their concept-rendered type); a for-loop over such
        # a param must type its iterator frame field against that template arg.
        proto_param_names = frozenset(
            pname for pname, ptype in func.params
            if self.functions.protocols.is_static_protocol_param(ptype))
        # A forwarded local (`xs = it`) iterated across a suspension reuses the
        # backing param's `T_<pname>` deduction rather than rendering its
        # protocol type as an un-instantiable concept.
        proto_param_alias = dict(func.forwarded_locals or {})
        uid_map: dict[int, int] = {}
        fields_out: list[tuple[str, str]] = []
        struct_names_out: dict[int, str] = {}
        # Same-module frame structs this loop machinery embeds by value; like
        # local_hoists below, committed to the state only after the walk
        # completes so a mid-walk `_CFGNotYetSupported` leaves nothing behind.
        dep_units_out: list[tuple[str, str | None]] = []
        # {TpyForEach -> GeneratorForInfo}: carries pointer_form_loop_var
        # so `setup_resumable_frame_locals` + the struct field emit render a
        # non-value loop var as an aliasing `T*` rather than a `frame_slot<T>`
        # value copy.
        for_loop_info: IdentityMap = IdentityMap()
        info_by_uid: dict[int, GeneratorForInfo] = {}
        counter = [0]
        # Loop-var hoists are accumulated here and applied to
        # func.generator_locals only after the walk completes, so a mid-walk
        # `_CFGNotYetSupported` (an unsupported for-loop shape) leaves no
        # partially-appended loop-var entries behind -- they would emit as
        # duplicate frame fields.
        local_hoists: list[tuple[str, "TpyType"]] = []

        def walk(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                if isinstance(s, TpyForEach) and (
                        s.is_async
                        or rcfg._stmts_have_any_suspension(s.body)
                        or rcfg._stmts_have_any_suspension(s.orelse)):
                    cur_uid = counter[0]
                    counter[0] += 1
                    uid_map[id(s)] = cur_uid
                    if s.is_async:
                        # async-for (M6): one `__aiter__` iterator slot; the
                        # advance is a Yield(await __anext__), not a strategy.
                        iter_t = self.types.get_resolved_type(s.iterable)
                        if iter_t is None:
                            raise CodeGenError(
                                "for-loop iterable has no resolved type "
                                "(async for pre-scan)", loc=s.loc)
                        src_cpp = self.types.type_to_cpp(unwrap_ref_type(iter_t))
                        iter_field_type = f"::tpy::aiter_type_t<{src_cpp}>"
                        fields_out.append(
                            (f"__for_itr_{cur_uid}", iter_field_type))
                        if s.async_aiter_type is None:
                            raise CodeGenError(
                                "async-for missing resolved aiter type "
                                "(internal: sema didn't populate "
                                "stmt.async_aiter_type)", loc=s.loc)
                        struct_names_out[cur_uid] = self._sub_struct_qualname(
                            s.async_aiter_type, "__anext__", loc=s.loc)
                        _anext_dep = rcfg.same_module_dep_unit(
                            s.async_aiter_type, "__anext__",
                            self.ctx.analyzer.ctx.module_name)
                        if _anext_dep is not None:
                            dep_units_out.append(_anext_dep)
                        info = GeneratorForInfo(
                            uid=cur_uid, strategy="async_for", fields=[])
                    else:
                        # Sync for-loop: reuse the legacy strategy analysis so
                        # range / begin_end peepholes (a plain counter / begin-
                        # end iterators) carry over instead of the slower
                        # universal `__iter__`/`__next__` shape. The fields it
                        # returns are emitted as `tpy::frame_slot<T>` in
                        # gen_coro_struct (matching hoisted user locals).
                        info = self.gen_generators._analyze_for_strategy(
                            s, cur_uid, proto_param_names=proto_param_names,
                            proto_param_alias=proto_param_alias)
                        if info is None:
                            raise rcfg._CFGNotYetSupported(
                                "yield inside this for-loop shape is not yet "
                                "supported on the resumable path.", loc=s.loc)
                        fields_out.extend(info.fields)
                        dep_units_out.extend(info.dep_units)
                    for_loop_info[s] = info
                    info_by_uid[cur_uid] = info
                    elem_t = (unwrap_ref_type(s.elem_type)
                              if s.elem_type else None)
                    if elem_t is None:
                        raise CodeGenError(
                            "for-with-suspension: loop var has no "
                            "resolved elem_type", loc=s.loc)
                    local_hoists.append((s.var, elem_t))
                if hasattr(s, "sub_bodies"):
                    for b in s.sub_bodies():
                        walk(b)

        walk(body)
        # Walk succeeded: commit the loop-var hoists.
        if local_hoists:
            if func.generator_locals is None:
                func.generator_locals = []
            existing = {n for n, _ in func.generator_locals}
            for name, elem_t in local_hoists:
                if name not in existing:
                    func.generator_locals.append((name, elem_t))
                    existing.add(name)
        state.frame_dep_units.extend(dep_units_out)
        state.for_uid_map = uid_map
        state.for_fields = fields_out
        state.async_for_struct_names = struct_names_out
        state.for_loop_info = for_loop_info
        state.for_info_by_uid = info_by_uid
        state.for_prescanned = True
        return uid_map

    def _classify_pointer_alias_locals(self, func: TpyFunction) -> 'set[str]':
        """Frame locals that are borrow ALIASES of existing storage, so their
        frame field must be a `T*` (aliasing the source) rather than an owning
        `frame_slot<T>` (which would copy across the suspension -- a silent
        reference-copy divergence from CPython).

        Three binding shapes, mirroring how sync codegen renders them as
        references (`Box& a = items[0]` / `auto&& a = tuple_elem_ref(...)`):
          - single-assign `a = <lvalue>` of a plain non-value type,
          - tuple-unpack borrow targets (`stmt.is_ref[i]`), and
          - a walrus `(a := <lvalue>)`, which sema already classified as a
            statement-level borrow.

        Const sources (readonly params, const tuple elements) join
        `const_pointer_alias_locals` so the field is `const T*`. Exception-
        handler bindings are excluded: the caught exception is only live inside
        the handler, so aliasing it across a suspension could dangle -- they
        keep the safe owning copy. Owning bindings (`a = Box(1)`, Own[T]
        elements, value types) also keep the owning frame_slot path.

        Cached on the resumable state; consumed by `gen_coro_struct` (field
        type) and `setup_resumable_frame_locals` (pointer_locals membership).
        Idempotent across the struct + body passes.
        """
        state = rcfg.resumable_state(func)
        if state.pointer_alias_prescanned:
            return state.pointer_alias_locals
        state.pointer_alias_prescanned = True
        if not func.generator_locals:
            return state.pointer_alias_locals
        frame_local_types = {n: t for n, t in func.generator_locals}
        aliases = state.pointer_alias_locals
        const_aliases = state.const_pointer_alias_locals
        body = self._effective_body(func)
        # Read per-function off the analyzer, not off ctx: this prescan runs at
        # struct-emit time, before the body scope that would seed ctx with it.
        analyzer = self.ctx.analyzer
        borrow_decls = analyzer.function_stmt_borrow_decls.get(func, {})
        ever_owned = analyzer.function_ever_owned_locals.get(func, set())

        exc_bindings: set[str] = set()

        def collect_exc(stmts: 'list[TpyStmt]') -> None:
            for s in stmts:
                if isinstance(s, TpyTry):
                    for h in s.handlers:
                        if h.binding is not None:
                            exc_bindings.add(h.binding)
                if hasattr(s, "sub_bodies"):
                    for b in s.sub_bodies():
                        collect_exc(b)

        collect_exc(body)

        def root_name(expr: 'TpyExpr | None') -> 'str | None':
            # Walk `.obj` (field access / subscript) down to the base name.
            cur = expr
            while cur is not None and not isinstance(cur, TpyName):
                cur = getattr(cur, "obj", None)
            return cur.name if isinstance(cur, TpyName) else None

        def walk(stmts: 'list[TpyStmt]') -> None:
            for s in stmts:
                if isinstance(s, TpyVarDecl):
                    ltype = frame_local_types.get(s.name)
                    # Exclude any source rooted at an exception-handler binding
                    # (`e`, `e.inner`, ...): the caught exception is handler-
                    # scoped and may not survive a suspension stably, so a frame
                    # pointer into it could dangle -- keep the owning copy.
                    src_is_exc = root_name(s.init) in exc_bindings
                    # Owned-erased (Own[dyn P]) locals move on binding --
                    # never a borrow alias of the source.
                    ltype_bare = unwrap_ref_type(ltype) if ltype is not None else None
                    # is_const_storage_source: a field chain rooted at a
                    # const receiver (self.field in a readonly method)
                    # renders const; sync locals pick that up via auto
                    # deduction, but the frame field must spell it. An
                    # element read off a deep-const-verdict param (the
                    # seeded name set) is a const source too -- the frame
                    # field spells the verdict, so the alias must match.
                    def init_is_const(s=s, ltype_bare=ltype_bare):
                        if (emit_prims.is_const_indirect(
                                    self.ctx, ltype_bare, s.init, s)
                                or self.ctx.is_const_storage_source(s.init)):
                            return True
                        src = s.init
                        if isinstance(src, TpyCoerce):
                            src = src.expr
                        if not (isinstance(src, TpySubscript)
                                and isinstance(src.obj, TpyName)
                                and src.obj.name
                                in self.ctx.deep_const_borrow_params):
                            return False
                        root_t = self.ctx.var_types.get(src.obj.name)
                        return isinstance(
                            unwrap_readonly(unwrap_ref_type(root_t)),
                            TupleType) if root_t is not None else False
                    if (ltype is not None and s.init is not None
                            and not src_is_exc
                            and not isinstance(ltype_bare, OwnType)
                            and emit_prims.is_plain_nonvalue(self.ctx, ltype_bare)
                            and not self.ctx.is_rvalue_source(s.init)):
                        aliases.add(s.name)
                        if init_is_const():
                            const_aliases.add(s.name)
                    elif (ltype is not None and s.init is not None
                            and not src_is_exc
                            and isinstance(ltype_bare, OptionalType)
                            and ltype_bare.uses_pointer_repr()
                            and init_is_const()):
                        # Pointer-repr Optional locals get their bare `T*`
                        # frame field on their own emission branch (not via
                        # `aliases`); only the const fact is recorded here,
                        # where the initializing decl is in hand.
                        const_aliases.add(s.name)
                elif isinstance(s, TpyWith) and s.is_async:
                    # An `async with ... as t` whose __aenter__ result is a
                    # borrow (pointer Poll payload): the as-binding must
                    # alias, not copy -- same rule as the await-init
                    # VarDecl arm above. Gated on the item's own
                    # enter_type, not frame_local_types: the with-prescan
                    # appends the as-target to generator_locals only
                    # later, at struct emission.
                    for it in s.items:
                        if (it.target is not None
                                and it.aenter_result_is_borrow
                                and it.enter_type is not None
                                and emit_prims.is_plain_nonvalue(
                                    self.ctx,
                                    unwrap_ref_type(it.enter_type))):
                            aliases.add(it.target)
                            if it.aenter_result_is_const:
                                const_aliases.add(it.target)
                elif isinstance(s, TpyTupleUnpack):
                    # For-loop element unpacks (`idx, it = __for_tup`) already
                    # get pointer-form slots via the loop machinery's
                    # pointer_form_unpack_targets; skip them here so they keep
                    # that path (and the `&std::get` emit) unchanged.
                    src_is_loop_holder = (
                        isinstance(s.value, TpyName)
                        and s.value.name.startswith("__for_tup_"))
                    # A frame `T*` alias is sound only when the source resolves
                    # to storage outliving the frame: a stable lvalue (name /
                    # field, bound by-ref) or a call/method-call returning a
                    # borrow tuple (`std::tuple<T*, ...>` -- pointers to external
                    # storage). A value-tuple rvalue temp (a tuple literal ->
                    # `std::tuple<T, ...>`) would leave the alias pointing into
                    # a destroyed stack temporary across the suspension, so it
                    # falls back to the owning-copy path instead.
                    src_safe_to_alias = (
                        not self.ctx.is_rvalue_source(s.value)
                        or isinstance(s.value, (TpyCall, TpyMethodCall)))
                    if not src_is_loop_holder and src_safe_to_alias:
                        src_const = emit_prims.unpack_source_has_const_slots(
                            self.ctx, s)
                        for i, tname in enumerate(s.targets):
                            if (tname is not None and tname in frame_local_types
                                    and i < len(s.is_ref) and s.is_ref[i]
                                    and emit_prims.is_plain_nonvalue(
                                        self.ctx,
                                        unwrap_ref_type(frame_local_types[tname]))):
                                aliases.add(tname)
                                elem_const = src_const or (
                                    i < len(s.is_const_ref) and s.is_const_ref[i])
                                if elem_const:
                                    const_aliases.add(tname)
                # A walrus binds in expression position, so it is invisible to
                # the statement arms above. Read sema's verdict rather than
                # re-deriving one from the source shape: the sync walrus render
                # is chosen from the same fact, so the frame field and the sync
                # local agree on alias-vs-own for the same program.
                for ne in walrus_bindings(s):
                    ltype = frame_local_types.get(ne.target)
                    if ltype is None or ne.target not in borrow_decls:
                        continue
                    if ne.target in ever_owned:
                        continue
                    # Same handler-scoped exclusion the VarDecl row applies:
                    # sema classifies a walrus off a caught exception as an
                    # ordinary lvalue borrow, but the referent dies with the
                    # catch block, so the frame keeps the owning copy.
                    if root_name(ne.value) in exc_bindings:
                        continue
                    if not emit_prims.is_plain_nonvalue(
                            self.ctx, unwrap_ref_type(ltype)):
                        continue
                    aliases.add(ne.target)
                    if borrow_decls[ne.target]:
                        const_aliases.add(ne.target)
                if hasattr(s, "sub_bodies"):
                    for b in s.sub_bodies():
                        walk(b)

        walk(body)
        return aliases

    def _sub_struct_qualname(
            self, owner: 'NominalType | None', method: str,
            inferred_type_args: 'tuple[TpyType, ...] | None' = None,
            *, module_qual: str | None = None,
            extra_template_args: 'list[str] | None' = None,
            loc=None) -> str:
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

        `extra_template_args` appends the deduced C++ type for each
        static-protocol param on the callee (the `T&&`-forwarding model
        over `decltype((arg))`; see `_extra_template_args_for_await`) --
        these correspond to the `T_<pname>` template args declared on the
        callee's struct.
        """
        return sub_struct_qualname(
            self.types, owner, method, inferred_type_args,
            module_qual=module_qual,
            extra_template_args=extra_template_args, loc=loc)


    def _protocol_param_positions(self, call: TpyExpr) -> list[int]:
        """Callee param positions carrying a static protocol -- one extra
        `T_<pname>` template arg on the sub-coro struct each.

        A pure signature test, so it settles whether a suspension needs the
        (rendering) template-arg computation at all without entering a body
        scope.
        """
        if not isinstance(call, (TpyCall, TpyMethodCall)):
            return []
        fi = call.resolved_function_info
        if fi is None or not fi.params:
            return []
        return [i for i, p in enumerate(fi.params)
                if self.functions.protocols.is_static_protocol_param(p.type)]

    def _extra_template_args_for_await(self, call: TpyExpr) -> list[str]:
        """Compute the per-static-protocol `T_<pname>` template-arg
        spellings for the sub-coro struct of an inline await.

        Each callee static-protocol param gets one extra template arg on
        the struct (see `_protocol_template_parts`). The sub-future field
        is constructed in place via the struct ctor `Coro(T_<pname>&& it_)`
        (not the factory), so the extra arg must equal the `T_<pname>` a
        forwarding ref would deduce from the call argument -- `U&` for an
        lvalue arg (the coroutine borrows it), `U` for an rvalue. Spelled
        via `::tpy::await_arg_capture_t<decltype((arg))>` (the double parens
        carry value category) rather than `remove_cvref_t`, which decays the
        reference and would break the in-place lvalue bind.

        The arguments go through the SAME render the emplace uses -- the whole
        list, positionally, so a routed body reads the spelling off its own
        lowered nodes. The field type has to name exactly what the ctor
        receives, so a coercion applied at one site and not the other (the
        narrowed-Optional unwrap, an `Own[T]` move-out) is an ill-formed C++
        build, not a style difference. Temps queued by that render are
        discarded -- decltype doesn't evaluate, and the same args re-render at
        emplace time.
        """
        positions = self._protocol_param_positions(call)
        if not positions:
            return []
        for i in positions:
            if i >= len(call.args):
                # Defensive: callee param without a corresponding arg
                # at the call site (defaults aren't supported on async
                # static-protocol params today; bail rather than emit
                # a malformed template arg).
                return []
            arg = call.args[i]
            unwrapped = arg
            while isinstance(unwrapped, TpyCoerce):
                unwrapped = unwrapped.expr
            if isinstance(unwrapped, _FRESH_COLLECTION_NODES):
                # A fresh collection literal/comprehension reaching HERE has no
                # concrete C++ type (the param is a concept) and no frame
                # storage to survive the suspension, so it can't back the
                # sub-future field. The uniform frame-temp hoist normally
                # replaces it with a frame-slot NAME before this runs
                # (async_await_proto_param_literal); this stays for the
                # positions that hoist cannot reach.
                raise CodeGenError(
                    "awaiting a coroutine with a protocol-typed parameter "
                    "does not yet support a collection literal argument; pass "
                    "a named iterable instead",
                    loc=getattr(arg, "loc", None) or call.loc)
        checkpoint = self.ctx.temps.checkpoint()
        rendered = self._emplace_args(call)
        self.ctx.temps.rollback_to(checkpoint)
        # `await_arg_capture_t` models the callee factory's `T&&` deduction:
        # lvalue arg -> `U&` (borrow), rvalue -> `U` (own). `decltype((arg))`
        # (double parens) carries the value category.
        return [f"::tpy::await_arg_capture_t<decltype(({rendered[i]}))>"
                for i in positions]


    def _record_with_target_payloads(self, stmt: TpyWith,
                                     state: 'rcfg.ResumableState') -> None:
        """Record the C++ payload each `as`-target's frame field is spelled
        from: `::tpy::with_enter_t<CM>`, the element form of `CM::__enter__()`.

        Whether the target aliases the manager or owns a fresh value is not
        decidable here -- a generic or inherited manager instantiates either way
        -- so the choice goes to C++, exactly as a for-loop element's does.

        Skipped when the enter type answers the question by itself, leaving the
        plain field its sibling value locals use: a value type has no aliasing
        question; `Own[T]` (which `is_value_type` reports as a value) returns by
        value, so the target owns; and a view-family (`str` / `bytes`) target is
        given owning storage by the `OWNED_STR` arm instead, which a source-form
        payload would override (it is tested first).

        Sync `with` only: an `async with` target is bound from `__aenter__`'s Poll
        payload, not from `__enter__` (which such a manager need not even
        define), and its field already takes the aliasing pointer form.
        """
        if stmt.is_async:
            return
        for item in stmt.items:
            if item.target is None or item.enter_type is None:
                continue
            enter_t = unwrap_ref_type(item.enter_type)
            resolved_enter = self.types.resolve_type(enter_t)
            if (resolved_enter.is_value_type()
                    or is_str_category(resolved_enter)
                    or is_bytes_category(resolved_enter)):
                # `with_owning_str_targets` already claims the whole view family
                # for owning storage, keyed on the str/bytes CATEGORY rather than
                # on what `__enter__` actually lends -- so a `-> StrView` target
                # owns as much as a `-> str` one, and nothing is left to defer.
                continue
            if (isinstance(resolved_enter, OptionalType)
                    and resolved_enter.uses_pointer_repr()):
                # A pointer-repr Optional answers alias-vs-own by itself (the
                # null pointer doubles as None) and has its own field kind; a
                # source-form payload would win the earlier arm and render
                # `frame_slot<T*>`, whose reads deref one level short.
                continue
            ctx_t = self.types.get_resolved_type(item.context_expr)
            if ctx_t is None:
                continue
            ctx_cpp = self.types.type_to_cpp(unwrap_ref_type(ctx_t))
            state.with_target_payloads[item.target] = (
                f"::tpy::with_enter_t<{ctx_cpp}>")

    def _prescan_with_manager_homes(self, func: TpyFunction,
                                    body: list[TpyStmt]) -> None:
        """Give an OWNED manager a frame field when its `as`-target's field points
        INTO it.

        Such a field holds a pointer or view into whatever `__enter__()` returned,
        so the manager has to outlive the FRAME, not the statement. A borrowed
        manager already does -- it aliases a frame-resident local -- but an owned
        one is a per-invocation local, so a read of the target after any later
        suspension would dangle. Only for a non-decomposed region: a decomposed
        one allocates the field regardless.

        Runs after `_prescan_with_stmts` so it can ask the frame-layout PLAN what
        each target's field actually is, rather than re-deriving it from the enter
        type's spelling -- an enumeration of spellings cannot close that set (a
        borrow-form tuple and a `Span` are value types holding pointers), and
        every miss is a use-after-free. The plan never reads this pass's output,
        so building it here is not circular.
        """
        state = rcfg.resumable_state(func)
        if state.with_manager_homes_prescanned:
            return
        state.with_manager_homes_prescanned = True
        layout = self._frame_layout(func)
        local_types = dict(func.generator_locals or [])
        counter = [state.with_ctx_counter]
        fields_out: list[tuple[str, str]] = []

        def walk(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                if (isinstance(s, TpyWith) and not s.is_async
                        and not rcfg._stmts_have_any_suspension(s.body)):
                    per_item: list[int | None] = []
                    for item in s.items:
                        ctx_t = (self.types.get_resolved_type(item.context_expr)
                                 if item.target is not None
                                 and not item.manager_borrowed
                                 and item.manager_owns_enter_result
                                 and self._with_field_points_into_manager(
                                     item.target, layout, local_types)
                                 else None)
                        if ctx_t is None:
                            per_item.append(None)
                            continue
                        cur_n = counter[0]
                        counter[0] += 1
                        fields_out.append(
                            (f"__with_ctx_{cur_n}",
                             self.types.type_to_cpp(unwrap_ref_type(ctx_t))))
                        per_item.append(cur_n)
                    if any(n is not None for n in per_item):
                        state.with_owned_ctx_map[s] = per_item
                if hasattr(s, "sub_bodies"):
                    for b in s.sub_bodies():
                        walk(b)

        walk(body)
        state.with_fields = list(state.with_fields) + fields_out
        state.with_ctx_counter = counter[0]

    # Frame-field kinds whose storage is entirely its own: nothing in the field
    # points at the manager, so an owned manager may stay a case-block local.
    _SELF_CONTAINED_FRAME_KINDS = frozenset({
        rcfg.FrameLocalKind.OWNED_STR,
        rcfg.FrameLocalKind.OWNING_TUPLE_SLOT,
        rcfg.FrameLocalKind.FRAME_SLOT,
    })

    def _with_field_points_into_manager(
            self, target: str, layout: 'rcfg.FrameLayoutPlan',
            local_types: 'dict[str, TpyType]') -> bool:
        """Does the target's frame field hold a pointer or view INTO the manager?

        Defaults to YES for any kind not provably self-contained: a miss in this
        direction is a use-after-free, while a false positive only keeps the
        manager alive longer than strictly needed.
        """
        verdict = layout.bindings.get(target)
        if verdict is None:
            return False  # not frame-resident: no field to point with
        if verdict.kind in self._SELF_CONTAINED_FRAME_KINDS:
            return False
        if verdict.kind is not rcfg.FrameLocalKind.VALUE:
            # PTR_ALIAS / OPT_PTR (pointer), BORROW_TUPLE / MIXED_TUPLE_SLOT
            # (pointer elements), SOURCE_FORM_SLOT (alias when the trait picks
            # the borrow form).
            return True
        # A bare VALUE field is self-contained only for the owning value
        # families; the rest (views, `Span`, `Ptr`, a user ValueType holding
        # either) carry a pointer inside a value type.
        ltype = local_types.get(target)
        if ltype is None:
            return True
        t = self.types.resolve_type(unwrap_ref_type(ltype))
        if is_free_copy_scalar(t) or is_big_int_type(t) or isinstance(t, OwnType):
            return False
        if is_str_category(t) and not is_str_view_type(t):
            return False
        if is_bytes_category(t) and not is_bytes_view_type(t):
            return False
        return True

    def _prescan_resumable_ptr_slots(
            self, func: TpyFunction, body: list[TpyStmt]) -> None:
        """Reserve one `std::optional<T>` frame field per rvalue write into
        a pointer-form frame local (decl init or rebind). The pointer field
        outlives every case block, so the materialization slot must too --
        an inline or hoisted per-call slot dies at the next suspension (or
        the next `__next__` call) while the pointer still aims at it.

        Which writes need a slot is `ptr_slot_field_type`'s call (shared
        with the pointer-local reseat lowering, which rejects on a
        missing entry). Fields land on `state.ptr_slot_fields`; the site
        map (`stmt -> field`) on `state.ptr_slot_map`, seeded into the
        body ctx by `setup_resumable_frame_locals`. Per-site fields (no
        shared rebind slot): an alias holding the previous value keeps a
        live target, and a loop's re-executed site destroys the prior
        payload at the rebind, like sync slot reuse.
        """
        state = rcfg.resumable_state(func)
        if state.ptr_slots_prescanned:
            return
        state.ptr_slots_prescanned = True
        layout = self._frame_layout(func)
        ptr_kinds = (rcfg.FrameLocalKind.PTR_ALIAS, rcfg.FrameLocalKind.OPT_PTR)
        ptr_names = {n for n, v in layout.bindings.items()
                     if v.kind in ptr_kinds}
        if not ptr_names:
            return
        local_types = dict(func.generator_locals or [])
        fields: list[tuple[str, str]] = []
        uid_map: IdentityMap = IdentityMap()

        def visit(stmt: TpyStmt, name: str, init: 'TpyExpr | None') -> None:
            if name not in ptr_names or init is None:
                return
            target_type = local_types.get(name)
            inner = (target_type.inner
                     if isinstance(target_type, OptionalType) else target_type)
            cpp_type = (self.types.type_to_cpp(unwrap_ref_type(inner))
                        if inner is not None else "auto")
            field_cpp = emit_prims.ptr_slot_field_type(
                self.ctx, init, target_type, cpp_type)
            if field_cpp is None:
                return
            fname = f"__ptr_slot_f{len(fields)}"
            fields.append((fname, field_cpp))
            uid_map[stmt] = fname

        def walk(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                if isinstance(s, TpyNestedDef):
                    continue
                if isinstance(s, TpyVarDecl):
                    visit(s, s.name, s.init)
                elif isinstance(s, TpyAssign) and isinstance(s.target, TpyName):
                    visit(s, s.target.name, s.value)
                if hasattr(s, "sub_bodies"):
                    for b in s.sub_bodies():
                        walk(b)

        walk(body)
        state.ptr_slot_fields = fields
        state.ptr_slot_map = uid_map

    def _prescan_with_stmts(
            self, func: TpyFunction,
            body: list[TpyStmt]) -> dict[int, list[int]]:
        """Find `with` stmts whose body contains await. For each, allocate
        a ctx_n per WithItem, declare a `__with_ctx_<n>` frame field of
        the context-manager's C++ type, and register any `as` target
        names as hoisted locals.

        Returns {id(TpyWith) -> [ctx_n_per_item]}; frame fields land on
        `state.with_fields` as `[(name, cpp_type)]`.
        """
        state = rcfg.resumable_state(func)
        if state.with_prescanned:
            return state.with_uid_map
        uid_map: dict[int, list[int]] = {}
        fields_out: list[tuple[str, str]] = []
        counter = [0]

        # Async-with sub-coro struct names by ctx_n. Populated alongside
        # the per-ctx frame field. Emit uses this to size __sub_<i>
        # slots and to write the inline factory call.
        struct_names_out: dict[int, tuple[str, str]] = {}
        # The `__aenter__`/`__aexit__` units whose structs those slots embed.
        dep_units_out: list[tuple[str, str | None]] = []

        def walk(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                if isinstance(s, TpyWith):
                    # A TARGET's frame field is NOT gated on the region being
                    # decomposed: the target outlives its statement (Python
                    # scoping), so it can be read across a later suspension the
                    # `with` itself does not contain. Record the payload its
                    # field is spelled from for every `with`; the manager field
                    # and state numbering below stay gated.
                    self._record_with_target_payloads(s, state)
                    # A DECOMPOSED region needs the manager in the frame anyway
                    # (its states are disjoint). Whether a NON-decomposed one
                    # needs it depends on the target's field kind, which the frame
                    # layout has not decided yet -- see
                    # `_prescan_with_manager_homes`, which runs once this pass has
                    # fed it the payloads it reads.
                # The MANAGER needs a frame field only when the region is
                # decomposed into states: a sync `with` whose body contains an
                # await (M3.2), or any `async with` (its `__aenter__` /
                # `__aexit__` are themselves the suspensions).
                if (isinstance(s, TpyWith)
                        and (s.is_async
                             or rcfg._stmts_have_any_suspension(s.body))):
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
                        if item.manager_borrowed:
                            state.with_borrowed_fields.add(f"__with_ctx_{cur_n}")
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
                            # Name each sub-coro struct from the method's
                            # DEFINING record (sema-stamped; an ancestor when
                            # the manager inherits __aenter__/__aexit__), not
                            # the manager's subclass type -- the struct exists
                            # only for the definer. Falls back to the manager
                            # type for own methods (owner == ctx_inner).
                            aenter_owner = item.aenter_owner_type or ctx_inner
                            aexit_owner = item.aexit_owner_type or ctx_inner
                            struct_names_out[cur_n] = (
                                self._sub_struct_qualname(
                                    aenter_owner, "__aenter__", loc=s.loc),
                                self._sub_struct_qualname(
                                    aexit_owner, "__aexit__", loc=s.loc),
                            )
                            for _dunder, _owner in (("__aenter__", aenter_owner),
                                                    ("__aexit__", aexit_owner)):
                                _dep = rcfg.same_module_dep_unit(
                                    _owner, _dunder,
                                    self.ctx.analyzer.ctx.module_name)
                                if _dep is not None:
                                    dep_units_out.append(_dep)
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
                                state.with_owning_str_targets.add(item.target)
                    uid_map[id(s)] = per_item
                if hasattr(s, "sub_bodies"):
                    for b in s.sub_bodies():
                        walk(b)

        walk(body)
        state.frame_dep_units.extend(dep_units_out)
        state.with_uid_map = uid_map
        state.with_fields = fields_out
        state.with_ctx_counter = counter[0]
        state.async_with_struct_names = struct_names_out
        state.with_prescanned = True
        return uid_map

    def _prescan_resumable_try_finally(
            self, func: TpyFunction,
            body: list[TpyStmt]) -> dict[int, int]:
        """Find try-stmts whose finally body contains an await. For
        each, allocate a uid and declare:
          * `__finally_exc_<n>` -- `std::exception_ptr` (always).
          * `__finally_pending_<n>` -- `bool` (only when try / handler /
             finally body contains a reachable `return`).
          * `__finally_ret_<n>` -- function's return type, for the
             same reason and only when the async def is non-void.
        Returns {id(TpyTry) -> uid}; field declarations land on
        `state.try_finally_fields`."""
        state = rcfg.resumable_state(func)
        if state.try_finally_prescanned:
            return state.try_finally_uid_map
        uid_map: dict[int, int] = {}
        fields_out: list[tuple[str, str]] = []
        counter = [0]
        is_void = self._is_void_return(func)
        ret_cpp = self._ret_cpp(func) if not is_void else None

        def walk(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                if (isinstance(s, TpyTry)
                        and s.finally_body
                        and rcfg._stmts_have_any_suspension(s.finally_body)):
                    cur_uid = counter[0]
                    counter[0] += 1
                    uid_map[id(s)] = cur_uid
                    fields_out.append(
                        (f"__finally_exc_{cur_uid}", "std::exception_ptr"))
                    has_return = (rcfg._stmts_have_any_return(s.try_body)
                                   or any(rcfg._stmts_have_any_return(h.body)
                                           for h in s.handlers)
                                   or rcfg._stmts_have_any_return(s.finally_body))
                    if has_return:
                        fields_out.append(
                            (f"__finally_pending_{cur_uid}", "bool"))
                        # Generator pending returns are always StopIteration;
                        # no value slot needed (_ret_cpp would give Iterator[T]).
                        if ret_cpp is not None and not self._is_generator_shape():
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
        state.try_finally_uid_map = uid_map
        state.try_finally_fields = fields_out
        state.try_finally_prescanned = True
        return uid_map

    def _make_await_payload(self, await_node: TpyAwait, host_stmt: TpyStmt,
                             kind, bind_target, return_stmt) -> 'rcfg.AwaitPayload':
        """CFGBuilder payload factory: derives mode + sub_field_cpp_type
        from the await's sema annotations."""
        if await_node.awaited_prebuilt_slot is not None:
            # Bound-coroutine handle: poll the handle's own frame slot in
            # place (INLINE flavors for reset/poll; no field, no emplace).
            operand_type = self.ctx.get_expr_type(await_node.value)
            inner = unwrap_readonly(unwrap_own(unwrap_ref_type(operand_type)))
            return rcfg.AwaitPayload(
                mode=rcfg.AwaitMode.INLINE,
                sub_field_cpp_type=self.types.type_to_cpp(inner),
                operand_expr=await_node.value,
                kind=kind,
                bind_target=bind_target,
                return_stmt=return_stmt,
                host_stmt=host_stmt,
                await_node=await_node,
                prebuilt_slot=await_node.awaited_prebuilt_slot,
            )
        dep_unit: 'tuple[str, str | None] | None' = None
        module_qual: str | None = None
        if await_node.awaited_async_func_name is not None:
            mode = rcfg.AwaitMode.INLINE
            inferred_type_args = getattr(
                await_node.value, "inferred_type_args", None)
            # Cross-module free-function await spelled `mod.func(...)`:
            # operand is a TpyMethodCall whose receiver is the module
            # (no class owner). Use the module qualifier to namespace
            # the sub-coro struct name.
            if (isinstance(await_node.value, TpyMethodCall)
                    and await_node.awaited_method_owner_type is None):
                module_qual = (await_node.value.user_module_call
                               or await_node.value.builtin_module_call)
            elif (isinstance(await_node.value, TpyCall)
                    and await_node.awaited_method_owner_type is None):
                # Bare `from mod import f; await f()`: qualify the coro struct
                # with the resolved function's defining module. originating_module
                # is chain-flattened + shadow-correct (unlike imported_names, which
                # maps the direct import); same module -> None -> bare name.
                fi = getattr(await_node.value, "resolved_function_info", None)
                cur = self.ctx.analyzer.ctx.module_name
                if (fi is not None and fi.originating_module is not None
                        and fi.originating_module != cur):
                    module_qual = fi.originating_module
            # The `T_<pname>` args for a static-protocol callee are NOT added
            # here: they must be spelled from the same render the emplace
            # passes to the ctor, and that render is only decidable under the
            # body scope (narrowing, move-out) which does not exist yet at CFG
            # build. Struct emit re-spells the name with them.
            sub_cpp = self._sub_struct_qualname(
                await_node.awaited_method_owner_type,
                await_node.awaited_async_func_name,
                inferred_type_args,
                module_qual=module_qual,
                loc=await_node.loc)
            # Same-module callee only -- a cross-module struct is already
            # complete via its header, and the bare-name unit key would match a
            # same-named local unit. The two forms carry that fact differently:
            # a method's owner type names its defining module, while for a free
            # function `module_qual` is the signal (it is only computed when
            # there is no owner type).
            _owner = await_node.awaited_method_owner_type
            if _owner is not None:
                dep_unit = rcfg.same_module_dep_unit(
                    _owner, await_node.awaited_async_func_name,
                    self.ctx.analyzer.ctx.module_name)
            elif module_qual is None:
                dep_unit = (await_node.awaited_async_func_name, None)
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
            dep_unit=dep_unit,
            sub_struct_module_qual=module_qual,
        )

    def _compute_case_entries(self, cfg: 'rcfg.CFG') -> dict[int, _StateLabel]:
        """Return mapping bb_id -> StateLabel for every BB that needs its
        own case label in the emitted switch. Cached on the CFG so the
        struct-emit and poll-emit passes share the result.

        Rules: a BB is a case entry iff it is
        - the entry BB (S_INITIAL),
        - the resume_bb of a Yield (S_RESUME_<i>),
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
        # Same for CFG-based finally EXIT BBs: a `return` inside the
        # finally body transitions state directly here without a CFG
        # edge, so the BB must have a case label even if the
        # Fall-from-finally-end region-crossing already picked it up.
        finally_entry_bbs: set[int] = set()
        finally_exit_bbs: set[int] = set()
        for bb in cfg.blocks.values():
            for r in bb.region_stack:
                if (isinstance(r, rcfg.TryRegion)
                        and r.finally_entry_bb is not None):
                    finally_entry_bbs.add(r.finally_entry_bb)
                elif (isinstance(r, rcfg.FinallyRegion)
                        and r.finally_exit_bb is not None):
                    finally_exit_bbs.add(r.finally_exit_bb)
        for bid in finally_entry_bbs | finally_exit_bbs:
            if bid not in case_entries:
                case_entries[bid] = _StateLabel(_StateKind.JOIN, join_idx)
                join_idx += 1
        # MatchDispatch join BBs: the dispatch transitions to its join
        # directly (a non-CFG edge from the suspension-free dispatch's
        # fall-through), and each arm body Falls there. The arm bodies
        # are INLINED by the dispatch (not their own cases), so arm_bbs
        # are deliberately NOT marked here; only the shared join needs a
        # label.
        match_join_bbs: set[int] = set()
        for bb in cfg.blocks.values():
            t = bb.terminator
            if isinstance(t, rcfg.MatchDispatch) and t.join_bb is not None:
                match_join_bbs.add(t.join_bb)
        for bid in match_join_bbs:
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
        # Frames with an abandonment-cleanup destructor must not be left
        # at a suspended state when an exception escapes -- the unwind
        # already ran the active regions' cleanup, so a later destruction
        # would re-run it. Mark the frame done before propagating.
        has_dtor = bool(self._dtor_cleanup_cases(cfg))
        if has_dtor:
            out.write(f"{inner}try {{\n")
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
        self._emit_resumable_done_case(out, inner)
        out.write(f"{inner}}}\n")
        if has_dtor:
            out.write(f"{inner}}} catch (...) {{\n")
            out.write(f"{inner}{INDENT}__state = S_DONE;\n")
            out.write(f"{inner}{INDENT}throw;\n")
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
                                 payload: 'rcfg.SuspensionPayload') -> bool:
        """True if emitting the Yield cannot throw. For a generator yield,
        the emitted action is `return <value>;` -- no-throw iff the value
        is a literal / simple name."""
        if isinstance(payload, rcfg.YieldPayload):
            return (payload.value_expr is None
                    or self._expr_is_simple(payload.value_expr))
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
        # Skip the try/catch wrap entirely for case bodies provably no-throw
        # (typical setup case). Computed here (not in the try loop below)
        # because the guard pre-allocation and the finally-frame push both
        # need it.
        no_throw = self._case_is_no_throw(cfg, entry_bb)
        try_emitting = ([] if no_throw else
                         [i for i, r in enumerate(bb.region_stack)
                          if isinstance(r, (rcfg.TryRegion,
                                            rcfg.WithRegion))])
        # Pre-allocate a `bool __fin_ran_N` guard name for each region whose
        # cleanup can run on an exit edge inside its C++ try and be re-run by
        # its own catch: a TryRegion with a finally, or any WithRegion. The
        # name is populated now so the finally frames pushed just below share
        # it (a return/break/continue via _emit_finally_chain sets the same
        # guard the region's catch tests); the `bool` is declared later, at
        # the right indent before each region's `try {`.
        for i in try_emitting:
            region = bb.region_stack[i]
            if ((isinstance(region, rcfg.TryRegion)
                 and region.finally_helper_name is not None)
                    or isinstance(region, rcfg.WithRegion)):
                self.ctx.finally_guard_counter += 1
                self.ctx.resumable_region_guards[region] = (
                    f"__fin_ran_{self.ctx.finally_guard_counter}")
        # Push FinallyContext entries so a `return` inside this case
        # body walks the right finally chain via _emit_finally_chain.
        # ExceptRegion contributes its parent try's finally because
        # Python runs finally after the handler completes.
        helpers = self._finally_helpers_for_region_stack(bb.region_stack)
        pushed_finally = self._push_finally_helpers(helpers)
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
            # Collect the (region, extras) stack as data first -- without
            # emitting -- so the body can be buffered before the guard
            # declarations are written.
            tryctx_stack: list[tuple[rcfg.Region, tuple[str, ...]]] = []
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
                tryctx_stack.append((region, tuple(extras)))

            # Buffer the body at the indent it will occupy inside all the
            # tries, so we learn which guards actually get set before
            # declaring them: a region whose cleanup never runs on a normal
            # exit edge (e.g. a with-body that always raises) sets no guard,
            # so its `bool` + catch tests are skipped -- the shared
            # live_finally_guards gate.
            self.ctx.indent_level += len(tryctx_stack)
            body_buf = io.StringIO()
            self._emit_case_body(body_buf, cfg, entry_bb, case_entries, func)
            self.ctx.indent_level -= len(tryctx_stack)

            for region, extras in tryctx_stack:
                guard = self._active_region_guard(region)
                if guard is not None:
                    out.write(f"{body_indent}bool {guard} = false;\n")
                out.write(f"{body_indent}try {{\n")
                self.ctx.indent_level += 1
                body_indent = self.ctx.indent()

            out.write(body_buf.getvalue())

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
            # Guards are scoped to this case's `{ }` block; drop the map and
            # the live-set so a later case reusing the same region object
            # can't read a stale name (each case re-declares its own).
            self.ctx.resumable_region_guards.clear()
            self.ctx.live_finally_guards.clear()

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
        on top of the stack).

        Each entry is `(kind, payload, region)`: `region` is the source
        region the entry came from, so a consumer can recover the region's
        exit guard from `resumable_region_guards` without a second walk
        (the frame-dtor consumers ignore it)."""
        helpers: list = []
        for region in region_stack:
            if isinstance(region, rcfg.TryRegion):
                if region.finally_helper_name is not None:
                    helpers.append(
                        ("helper", region.finally_helper_name, region))
            elif isinstance(region, rcfg.ExceptRegion):
                if region.parent_finally is not None:
                    helpers.append(("helper", region.parent_finally, region))
            elif isinstance(region, rcfg.WithRegion):
                helpers.append(("with", region, region))
        return helpers

    def _active_region_guard(self, region: 'rcfg.Region') -> 'str | None':
        """The region's guard name, but only if some exit edge in this case
        actually set it -- so a catch tests (and _emit_case declares) a guard
        only when it can fire. Returns None for a region whose cleanup never
        runs on a normal exit (the guard would be dead)."""
        guard = self.ctx.resumable_region_guards.get(region)
        if guard is not None and guard in self.ctx.live_finally_guards:
            return guard
        return None

    def _dtor_cleanup_cases(self, cfg: 'rcfg.CFG') -> list[tuple[str, list]]:
        """Per suspended resume state, the cleanup actions (innermost-
        first) the frame destructor must run when the frame is dropped
        while suspended there -- the C++ analog of CPython closing a
        suspended generator (GeneratorExit running pending finallies and
        with.__exit__). States with nothing pending are omitted; when the
        result is empty no destructor is emitted at all.

        CFG-based finallies (the finally body itself suspends) cannot run
        inside a destructor and are skipped here; sema warns at the
        yield-in-finally site instead.
        """
        cases: list[tuple[str, list]] = []
        for y in cfg.yield_sites:
            bb = cfg.blocks[y.resume_bb]
            actions = list(reversed(
                self._finally_helpers_for_region_stack(bb.region_stack)))
            if actions:
                label = _StateLabel(_StateKind.RESUME, y.suspension_index)
                cases.append((label.cpp_name(), actions))
        return cases

    def _emit_frame_dtor(self, out: "TextIO", struct_name: str,
                         dtor_cases: list[tuple[str, list]]) -> None:
        """Emit the abandonment-cleanup destructor plus the defaulted move
        ctor it suppresses (frame_state neuters the moved-from source, so
        memberwise move stays safe without enumerating frame fields).

        Cleanup code raising during destruction panics: the destructor is
        noexcept and CPython's print-and-ignore has no TPy analog --
        fail-fast is the documented divergence.
        """
        kind = ("generator" if self._is_generator_shape() else "coroutine")
        out.write(f"{INDENT}{struct_name}({struct_name}&&) = default;\n")
        out.write(f"{INDENT}~{struct_name}() {{\n")
        inner = INDENT * 2
        body = INDENT * 3
        action_ind = INDENT * 4
        # CPython closes a suspended generator by throwing GeneratorExit
        # into it, so __exit__ observes an exceptional exit. Mirror the
        # contract: pass a GeneratorExit as exc_val to every with-region
        # __exit__ that takes one. The suppression bool is discarded --
        # nothing can resume inside a destructor.
        needs_ge = any(
            tag == "with" and payload.item.exit_takes_exc_val
            for _, actions in dtor_cases
            for tag, payload, _region in actions)
        if needs_ge:
            out.write(f"{inner}::tpy::GeneratorExit __tpy_ge{{}};\n")
        out.write(f"{inner}try {{\n")
        out.write(f"{body}switch (__state) {{\n")
        # Group states sharing an identical cleanup chain under one body.
        def chain_key(actions: list) -> tuple:
            return tuple((tag, payload if tag == "helper" else id(payload))
                         for tag, payload, _region in actions)
        grouped: dict[tuple, tuple[list[str], list]] = {}
        for state_name, actions in dtor_cases:
            entry = grouped.setdefault(chain_key(actions), ([], actions))
            entry[0].append(state_name)
        for state_names, actions in grouped.values():
            for sn in state_names:
                out.write(f"{body}case {sn}:\n")
            for tag, payload, _region in actions:
                if tag == "helper":
                    out.write(f"{action_ind}this->{payload}();\n")
                else:
                    self._emit_with_exit(out, action_ind, payload,
                                         on_exception=False,
                                         exc_val_cpp="&__tpy_ge")
            out.write(f"{action_ind}break;\n")
        out.write(f"{body}default: break;\n")
        out.write(f"{body}}}\n")
        out.write(f"{inner}}} catch (...) {{\n")
        out.write(f"{body}::tpy::tpy_panic(\"exception in 'finally' cleanup "
                  f"while destroying abandoned {kind}\");\n")
        out.write(f"{inner}}}\n")
        out.write(f"{INDENT}}}\n\n")

    def _pending_return_info_for_region_stack(
            self, region_stack: tuple) -> 'tuple[str, str | None, int, int] | None':
        """If a CFG-based finally is active for this region_stack, return
        `(flag, slot, target_bb, boundary)` for the INNERMOST one --
        boundary is the count of finally_stack frames contributed by
        helper-based regions BELOW it (those defer to AsyncFinallyExit
        instead of running at the return site). Returns None otherwise.

        Walking innermost-first is what makes nested CFG finally work:
        a return inside the inner try body parks into the inner's slot;
        the inner's AsyncFinallyExit later forwards into the outer.
        At the inner's `finally_exit_bb`, the inner FinallyRegion is no
        longer on the stack, so the same lookup naturally finds the
        outer (the new innermost) -- which is how AsyncFinallyExit
        learns whether to replay locally or forward outward.

        For try/handler-body returns: target_bb = finally_entry_bb (the
        return parks + jumps to the finally entry).
        For finally-body returns: target_bb = finally_exit_bb (the return
        parks + jumps straight to AsyncFinallyExit; running the finally
        body again would re-execute it from the top)."""
        innermost: 'tuple[str, str | None, int, int] | None' = None
        boundary = 0
        for region in region_stack:
            if isinstance(region, rcfg.TryRegion):
                if region.pending_return_flag is not None:
                    innermost = (region.pending_return_flag,
                                 region.pending_return_slot,
                                 region.finally_entry_bb,
                                 boundary)
                elif region.finally_helper_name is not None:
                    boundary += 1
            elif isinstance(region, rcfg.ExceptRegion):
                if region.parent_pending_return_flag is not None:
                    innermost = (region.parent_pending_return_flag,
                                 region.parent_pending_return_slot,
                                 region.parent_finally_entry_bb,
                                 boundary)
                elif region.parent_finally is not None:
                    boundary += 1
            elif isinstance(region, rcfg.FinallyRegion):
                if region.pending_return_flag is not None:
                    innermost = (region.pending_return_flag,
                                 region.pending_return_slot,
                                 region.finally_exit_bb,
                                 boundary)
            elif isinstance(region, rcfg.WithRegion):
                boundary += 1
        return innermost

    def _emit_finally_helper_call(self, out: "TextIO", indent: str,
                                    helper_name: str) -> None:
        """Emit `this->helper_name();`.

        The stop-check (`if __finally_stop`) is NOT emitted here. Callers
        that need to act on __finally_stop (e.g. to suppress a rethrow or
        skip a state transition) call _emit_generator_stop_check AFTER all
        cleanup for the current exit event has run, so that outer region
        cleanup (with.__exit__, outer finallies) is never skipped."""
        out.write(f"{indent}this->{helper_name}();\n")

    def _emit_generator_stop_check(self, out: "TextIO", indent: str) -> None:
        """Emit `if (this->__finally_stop) { return StopIteration; }`.

        Only emitted when in generator shape and the struct has __finally_stop.
        Call AFTER all cleanup for an exit event (region loop, catch handler)
        has run, so outer cleanups are never skipped by an early return."""
        if not (self._is_generator_shape() and self.ctx.generator_has_finally_stop):
            return
        done_state = self.ctx.generator_resumable_done_state or "S_DONE"
        out.write(f"{indent}if (this->__finally_stop) {{\n")
        out.write(f"{indent}{INDENT}__state = {done_state};\n")
        out.write(f"{indent}{INDENT}return ::tpy::make_unexpected("
                  f"::tpy::StopIteration{{}});\n")
        out.write(f"{indent}}}\n")

    def _push_finally_helpers(self, helpers: list) -> int:
        """Push FinallyContext entries for each helper. Returns the
        count pushed for matching pop in a `finally:` clause.

        Each frame links to its source region's exit guard (looked up in
        `resumable_region_guards` from the region carried by the helper
        entry) so a return/break/continue via _emit_finally_chain sets the
        same `bool` the region's catch tests -- no double-run of a raising
        cleanup on the return path."""
        count = 0
        for kind, payload, region in helpers:
            if kind == "helper":
                helper_name = payload
                def _emit_finally(o: "TextIO", ind: str,
                                  n=helper_name) -> None:
                    self._emit_finally_helper_call(o, ind, n)
            else:  # "with"
                with_region = payload
                def _emit_finally(o: "TextIO", ind: str,
                                  r=with_region) -> None:
                    self._emit_with_exit(o, ind, r, on_exception=False)
            guard = self.ctx.resumable_region_guards.get(region)
            fctx = FinallyContext(
                emit_finally=_emit_finally, terminates=False, loop_depth=0,
                guard_name=guard)
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
        # A normal-exit __exit__ copy that raised IS this manager's cleanup
        # exception: never call __exit__ with it, never offer it for
        # suppression -- just propagate. Only when such a copy exists (guard
        # set on some exit edge); a with-body with no normal exit needs none.
        guard = self._active_region_guard(region)
        if emit_tpy_catch:
            out.write(f" catch (::tpy::BaseException& __exc_{n}) {{\n")
            self.ctx.indent_level += 1
            catch_ind = self.ctx.indent()
            if guard is not None:
                out.write(f"{catch_ind}if ({guard}) throw;\n")
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
        if guard is not None:
            out.write(f"{catch_ind}if ({guard}) throw;\n")
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
        builder = rcfg.resumable_state(func).cfg_builder
        yield_for_case = self._yield_at_resume(cfg, case_entry_bb)
        # Helpers to invoke on throw from inside the handler body
        # (innermost first): each extra_finally + this region's own
        # finally. Mirrors the catch-all unwind order.
        handler_throw_finallies: tuple[str, ...] = tuple(extra_finallies)
        if region.finally_helper_name is not None:
            handler_throw_finallies = handler_throw_finallies + (region.finally_helper_name,)
        for handler in region.handlers:
            emit_prims.emit_except_handler_header(self.ctx, out, handler)
            self.ctx.indent_level += 1
            catch_indent = self.ctx.indent()
            # Reset the in-flight sub-future first action in catch.
            # Generators have no sub-futures; only reset for async shape.
            if (yield_for_case is not None
                    and isinstance(yield_for_case.payload, rcfg.AwaitPayload)):
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
            # A helper-based finally runs on the handler's NORMAL exit (via
            # _emit_exit_region_finallies during the walk) and would be
            # re-run by this nested catch if it raised. Guard it like the
            # main-try catch-all: one guard for the nested try, set before
            # any normal-exit copy, tested before the catch re-runs them.
            handler_guard: str | None = None
            if has_throw_unwind and not cfg_finally:
                self.ctx.finally_guard_counter += 1
                handler_guard = f"__fin_ran_{self.ctx.finally_guard_counter}"
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
            saved_guards: IdentityMap = IdentityMap()
            if handler_entry is not None:
                handler_bb = cfg.blocks[handler_entry]
                handler_stack_helpers = self._finally_helpers_for_region_stack(
                    handler_bb.region_stack)
                # Route the handler's normal-exit finallies (emitted per
                # region by _emit_exit_region_finallies) at this nested try's
                # guard, so a raising copy is not re-run by the inner catch.
                if handler_guard is not None:
                    for r in handler_bb.region_stack:
                        saved_guards[r] = (
                            self.ctx.resumable_region_guards.get(r))
                        self.ctx.resumable_region_guards[r] = handler_guard
            self.ctx.finally_stack = []
            # Link the handler frames to handler_guard (written into
            # resumable_region_guards just above), so a return/break/continue
            # inside the handler shares the guard the nested catch tests.
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
            # Buffer the walk at the depth it will occupy inside the (not yet
            # emitted) nested try, so a handler_guard that no normal-exit copy
            # sets is dropped along with its catch test -- the with/try-region
            # gate, applied to the handler's own nested try.
            walk_buf = io.StringIO()
            if has_throw_unwind:
                self.ctx.indent_level += 1
            try:
                if handler_entry is not None:
                    self._walk_inline(walk_buf, cfg, handler_entry,
                                       case_entries, func)
            finally:
                if has_throw_unwind:
                    self.ctx.indent_level -= 1
                self.ctx.in_except_tier = old_except_tier
                self.ctx.finally_stack = old_finally_stack
                self._restore_pending_return_ctx(prev_pending)
                # NOT `region`: that name holds the try region this whole
                # method emits for, and a Python for-loop variable outlives
                # its loop.
                for saved_region, prev in saved_guards.items():
                    if prev is None:
                        self.ctx.resumable_region_guards.pop(saved_region, None)
                    else:
                        self.ctx.resumable_region_guards[saved_region] = prev
            hg = (handler_guard
                  if handler_guard is not None
                  and handler_guard in self.ctx.live_finally_guards
                  else None)
            if hg is not None:
                out.write(f"{catch_indent}bool {hg} = false;\n")
            if has_throw_unwind:
                out.write(f"{catch_indent}try {{\n")
                self.ctx.indent_level += 1
            out.write(walk_buf.getvalue())
            if has_throw_unwind:
                self.ctx.indent_level -= 1
                inner_close = self.ctx.indent()
                out.write(f"{inner_close}}} catch (...) {{\n")
                self.ctx.indent_level += 1
                inner_catch = self.ctx.indent()
                if hg is not None:
                    # A normal-exit finally copy already ran (and raised, so
                    # we're here): skip re-running, just rethrow.
                    out.write(f"{inner_catch}if (!{hg}) {{\n")
                    self.ctx.indent_level += 1
                    body_ic = self.ctx.indent()
                    for helper in handler_throw_finallies:
                        self._emit_finally_helper_call(out, body_ic, helper)
                    self._emit_generator_stop_check(out, body_ic)
                    self.ctx.indent_level -= 1
                    out.write(f"{inner_catch}}}\n")
                    out.write(f"{inner_catch}throw;\n")
                elif cfg_finally:
                    assert region.finally_entry_bb is not None
                    fe_label = case_entries[region.finally_entry_bb].cpp_name()
                    for helper in handler_throw_finallies:
                        self._emit_finally_helper_call(out, inner_catch, helper)
                    out.write(f"{inner_catch}this->{region.captured_exc_field}"
                              f" = std::current_exception();\n")
                    out.write(f"{inner_catch}__state = {fe_label};\n")
                    out.write(f"{inner_catch}continue;\n")
                else:
                    for helper in handler_throw_finallies:
                        self._emit_finally_helper_call(out, inner_catch, helper)
                    self._emit_generator_stop_check(out, inner_catch)
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
        if (yield_for_case is not None
                and isinstance(yield_for_case.payload, rcfg.AwaitPayload)):
            self._emit_sub_reset(out, catch_indent,
                                  yield_for_case.payload,
                                  yield_for_case.suspension_index)
        for helper in extra_finallies:
            self._emit_finally_helper_call(out, catch_indent, helper)
        if region.captured_exc_field is not None:
            assert region.finally_entry_bb is not None
            label = case_entries[region.finally_entry_bb].cpp_name()
            out.write(f"{catch_indent}this->{region.captured_exc_field} "
                      f"= std::current_exception();\n")
            out.write(f"{catch_indent}__state = {label};\n")
            out.write(f"{catch_indent}continue;\n")
        else:
            # If an exit-edge copy of this finally already ran (and raised,
            # which is why we're in the catch), skip re-running it and just
            # rethrow the copy's own exception. Only when such a copy exists.
            guard = self._active_region_guard(region)
            if region.finally_helper_name is not None and guard is not None:
                out.write(f"{catch_indent}if (!{guard}) {{\n")
                self.ctx.indent_level += 1
                self._emit_finally_helper_call(
                    out, self.ctx.indent(), region.finally_helper_name)
                self._emit_generator_stop_check(out, self.ctx.indent())
                self.ctx.indent_level -= 1
                out.write(f"{catch_indent}}}\n")
                out.write(f"{catch_indent}throw;\n")
            else:
                if region.finally_helper_name is not None:
                    self._emit_finally_helper_call(
                        out, catch_indent, region.finally_helper_name)
                # All cleanup done; Python `return` in finally suppresses the
                # exception (return StopIteration rather than rethrowing).
                self._emit_generator_stop_check(out, catch_indent)
                out.write(f"{catch_indent}throw;\n")
        self.ctx.indent_level -= 1
        out.write(f"{indent}}}")

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
        # Resume step. Generators have no sub-future to poll and bind
        # nothing back in, so their resume case is a bare continuation.
        y = self._yield_at_resume(cfg, entry_bb)
        if y is not None and not self._is_generator_shape():
            self._emit_resume_core(out, body_indent, y.payload,
                                    y.suspension_index, func)
            if y.payload.kind is rcfg.AwaitKind.RETURN:
                # Resume core already emitted the return.
                return
        # A case may be (re-)entered after a suspension that split a
        # narrowed region; re-establish the narrowed bindings active on
        # entry before walking, so post-suspension member access sees the
        # narrowed type rather than the raw frame field.
        entry = cfg.blocks[entry_bb].entry_narrowings
        tok = self._emit_resume_narrowings(out, entry)
        self._walk_inline(out, cfg, entry_bb, case_entries, func,
                          chain_entry=entry)
        self._restore_resume_narrowings(tok)

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
        guards = self.ctx.resumable_region_guards
        # Innermost first.
        for region in reversed(exited):
            # Set the region's guard (if its C++ try is still open in this
            # case) before its cleanup copy runs, so the region's own catch
            # skips re-running a copy that raised.
            guard = guards.get(region)
            if isinstance(region, rcfg.TryRegion):
                if region.finally_helper_name is not None:
                    if guard is not None:
                        self.ctx.live_finally_guards.add(guard)
                        out.write(f"{indent}{guard} = true;\n")
                    self._emit_finally_helper_call(
                        out, indent, region.finally_helper_name)
            elif isinstance(region, rcfg.ExceptRegion):
                # Leaving an except handler normally: run the parent
                # try's finally body (Python semantics).
                if region.parent_finally is not None:
                    if guard is not None:
                        self.ctx.live_finally_guards.add(guard)
                        out.write(f"{indent}{guard} = true;\n")
                    self._emit_finally_helper_call(
                        out, indent, region.parent_finally)
            elif isinstance(region, rcfg.WithRegion):
                # Leaving a with-region normally: __exit__(None, None, None).
                if guard is not None:
                    self.ctx.live_finally_guards.add(guard)
                    out.write(f"{indent}{guard} = true;\n")
                self._emit_with_exit(out, indent, region,
                                            on_exception=False)
        # Emit stop-check only when to_regions has no pending cleanup of its
        # own. If to_regions still has with/__exit__ or finally helpers, the
        # state machine will run those in subsequent states; the stop-check
        # in those later transitions fires after ALL cleanup is done.
        if not _regions_have_pending_cleanup(to_regions):
            self._emit_generator_stop_check(out, indent)

    def _emit_resume_narrowings(
        self, out: "TextIO",
        entry_narrowings: 'dict[str, TpyType]',
        outer: 'dict[str, TpyType] | None' = None,
    ) -> '_NarrowingToken | None':
        """Re-establish isinstance / `is not None` narrowing bindings that
        are active on entry to a resumable BB. A suspension splits a
        narrowed region across C++ case scopes, so the original
        dynamic_cast / std::get local does not survive; this re-emits it
        from the sema-computed facts stamped on the BB. `outer` lists facts
        already bound in the enclosing C++ scope (passed at branch arms) so
        they are not re-cast redundantly. Returns a restore token (or None
        when nothing was emitted)."""
        if not entry_narrowings:
            return None
        if outer:
            delta = {k: v for k, v in entry_narrowings.items()
                     if outer.get(k) is not v}
        else:
            delta = entry_narrowings
        if not delta:
            return None
        lit_snap = self.ctx.save_literal_facts()
        proto_snap = self.ctx.save_protocol_narrowings()
        nv_saved = emit_prims.emit_isinstance_extractions(
            self.ctx, self.types, self.functions.protocols,
            out, delta, indent_extra=0)
        return (nv_saved, proto_snap, lit_snap)

    def _restore_resume_narrowings(self, token: '_NarrowingToken | None') -> None:
        if token is None:
            return
        nv_saved, proto_snap, lit_snap = token
        self.ctx.restore_narrowed_vars(nv_saved)
        self.ctx.restore_protocol_narrowings(proto_snap)
        self.ctx.restore_literal_facts(lit_snap)

    def _walk_inline(self, out: "TextIO", cfg: 'rcfg.CFG',
                     start_bb: int, case_entries: dict[int, _StateLabel],
                     func: TpyFunction,
                     chain_entry: 'dict[str, TpyType] | None' = None) -> None:
        """Walk BBs starting from start_bb, emitting their statements
        and following Fall/Branch terminators inline. Stops when the
        terminator is Yield/Return/Raise/Unreachable, or when a
        Fall/Branch target is a case_entry (then emits a state
        transition).

        `chain_entry` is the narrowing-fact set already bound in this C++
        scope (the entry_narrowings of start_bb, which every inline
        Fall-successor shares); branch arms diff their own facts against
        it so enclosing narrowings aren't re-cast."""
        chain_entry = chain_entry or {}
        body_indent = self.ctx.indent()
        cur = start_bb
        while True:
            bb = cfg.blocks[cur]
            # Emit BB statements.
            for stmt in bb.stmts:
                if isinstance(stmt, rcfg.AsyncForIterSetup):
                    self._emit_async_for_iter_setup(out, body_indent, stmt, func)
                elif isinstance(stmt, rcfg.WithEnter):
                    self._emit_with_enter(out, body_indent, stmt)
                elif isinstance(stmt, rcfg.AsyncWithSetup):
                    self._emit_async_with_setup(out, body_indent, stmt)
                elif isinstance(stmt, rcfg.AsyncFinallyExit):
                    self._emit_async_finally_exit(out, body_indent, stmt)
                else:
                    self._leaf.emit_leaf_stmt(
                        out, stmt, self.ctx.indent_level)
            t = bb.terminator
            if isinstance(t, rcfg.Yield):
                self._emit_yield_terminator(out, body_indent, t, func)
                return
            if isinstance(t, rcfg.ReturnT):
                # The return maker walks the active finally chain
                # (ctx.finally_stack) before emitting the Poll<T>::ready(...);
                # a routed body swaps only the VALUE render inside that
                # scaffolding (_async_return_value_cpp). The surrounding
                # comment/temp emission mirrors the linear statement walk --
                # minus its top-level line tracking, which is module-init
                # state and module init never emits a resumable frame.
                stmt_indent = self.ctx.indent()
                comment_loc = (
                    None if getattr(t.return_stmt, "no_source_comment", False)
                    else t.return_stmt.loc)
                self.ctx.emit_inline_comments(out, comment_loc, stmt_indent)
                code = self._resumable_return_code(t.return_stmt, stmt_indent)
                self.ctx.emit_source_comment(out, comment_loc, stmt_indent)
                # The maker's renders can queue temps; they belong ahead of
                # the code that reads them, so flush only after it is built.
                self.ctx.temps.flush(out, stmt_indent)
                out.write(code)
                return
            if isinstance(t, rcfg.RaiseT):
                # Emit the raise as an ordinary TpyRaise statement; the
                # finally chain is run via C++ exception unwinding.
                self._leaf.emit_leaf_stmt(
                    out, t.raise_stmt, self.ctx.indent_level)
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
                cond_cpp = self._leaf.render_cond(t.cond)
                self.ctx.temps.flush(out, body_indent)
                out.write(f"{body_indent}if ({cond_cpp}) {{\n")
                self.ctx.indent_level += 1
                self._walk_inline_or_jump(out, cfg, t.then_bb, case_entries,
                                            func, from_bb=cur,
                                            outer_narrowings=chain_entry)
                self.ctx.indent_level -= 1
                out.write(f"{body_indent}}} else {{\n")
                self.ctx.indent_level += 1
                self._walk_inline_or_jump(out, cfg, t.else_bb, case_entries,
                                            func, from_bb=cur,
                                            outer_narrowings=chain_entry)
                self.ctx.indent_level -= 1
                out.write(f"{body_indent}}}\n")
                return
            if isinstance(t, rcfg.AsyncForAdvance):
                self._emit_async_for_advance(
                    out, body_indent, cfg, t, case_entries, func, from_bb=cur)
                return
            if isinstance(t, rcfg.MatchDispatch):
                self._emit_match_dispatch(
                    out, cfg, t, case_entries, func, from_bb=cur)
                return
            raise CodeGenError(
                f"internal: unknown terminator {type(t).__name__}",
                loc=None)

    def _emit_match_dispatch(self, out: "TextIO", cfg: 'rcfg.CFG',
                              t: 'rcfg.MatchDispatch',
                              case_entries: dict[int, _StateLabel],
                              func: TpyFunction, from_bb: int) -> None:
        """Emit a suspending `match` (H1) by reusing the ordinary
        match dispatch for the type-aware tiers, with each arm body routed
        back through `_walk_inline` via the `_emit_case_body` hook. Arm
        bodies are inlined here (not their own cases); their internal
        suspensions split out resume cases as usual. After the dispatch,
        the (non-exhaustive) fall-through transitions to `join_bb`."""
        body_indent = self.ctx.indent()
        # The arm emitter inlines one arm body in place. `match` adds no
        # region, so the arm BB shares `from_bb`'s region stack -- the
        # inline walk handles its own fall-to-join / suspension exits.
        def emit_arm(arm_bb: int) -> None:
            # The dispatch emits the arm's first-BB subject narrowing (__case_N);
            # pass the arm's stamped facts as chain_entry so a nested branch
            # inside the arm diffs against the already-active subject fact
            # (avoids a redundant re-cast). Resume cases inside the arm
            # re-establish the narrowing via the generic _emit_case_body path.
            self._walk_inline(out, cfg, arm_bb, case_entries, func,
                              chain_entry=cfg.blocks[arm_bb].entry_narrowings)
        # The whole dispatch emits through the THIR match tiers; the hook
        # walks each arm's BB chain in place.
        arm_bb_by_body = {
            id(case.body): arm_bb
            for case, arm_bb in zip(t.match_stmt.cases, t.arm_bbs)
        }

        def arm_hook(body_key: int, lvl: int) -> None:
            # The tier hands the arm-body depth; the walker's renders key off
            # ctx.indent_level, so scope it to the arm.
            old_level = self.ctx.indent_level
            self.ctx.indent_level = lvl
            try:
                emit_arm(arm_bb_by_body[body_key])
            finally:
                self.ctx.indent_level = old_level
        self._leaf.emit_match_dispatch(out, t.match_stmt,
                                       self.ctx.indent_level, arm_hook)
        # The dispatch case MUST end in a terminating statement: a switch
        # whose cases all return/continue still "may fall through" to the
        # GCC eye (no default), so without this the next state's `case`
        # label trips -Werror=implicit-fallthrough. When a fall-through is
        # reachable (`join_bb` set) emit the state transition to join;
        # otherwise the match is exhaustive AND every arm terminates, so
        # the post-dispatch point is genuinely unreachable.
        if t.join_bb is not None:
            self._walk_inline_or_jump(
                out, cfg, t.join_bb, case_entries, func, from_bb=from_bb)
        else:
            out.write(f"{body_indent}__builtin_unreachable();\n")

    def _walk_inline_or_jump(self, out: "TextIO", cfg: 'rcfg.CFG',
                              target_bb: int,
                              case_entries: dict[int, _StateLabel],
                              func: TpyFunction,
                              from_bb: int,
                              outer_narrowings: 'dict[str, TpyType] | None' = None) -> None:
        body_indent = self.ctx.indent()
        if target_bb in case_entries:
            # The target is its own case; it re-establishes narrowings from
            # its own entry_narrowings (it may be re-entered after a
            # suspension, so the binding can't be carried via this edge).
            self._emit_exit_region_finallies(
                out, body_indent,
                cfg.blocks[from_bb].region_stack,
                cfg.blocks[target_bb].region_stack)
            out.write(f"{body_indent}__state = "
                      f"{case_entries[target_bb].cpp_name()};\n")
            out.write(f"{body_indent}continue;\n")
        else:
            entry = cfg.blocks[target_bb].entry_narrowings
            tok = self._emit_resume_narrowings(out, entry,
                                               outer=outer_narrowings)
            self._walk_inline(out, cfg, target_bb, case_entries, func,
                              chain_entry=entry)
            self._restore_resume_narrowings(tok)

    def _emit_async_finally_exit(self, out: "TextIO", indent: str,
                                   stmt: 'rcfg.AsyncFinallyExit') -> None:
        """Emit the deferred-return check + saved-exception rethrow at
        the dedicated finally exit BB. Both fields are cleared before
        extraction so a re-entry to the same try (e.g. in a loop)
        starts with no carried-over state.

        Order matters: the pending-return check runs FIRST so a `return`
        in the finally body wins over an in-flight exception captured
        from the try (Python: return-in-finally swallows). The deferred
        return clears the captured-exc field on the way out so the
        rethrow check is skipped automatically.

        The pending branch has two outcomes depending on whether an
        enclosing CFG-finally region is active at this exit site (set
        in `ctx.async_pending_return_*` by `_emit_case` from this BB's
        region_stack):
          * Not active: replay locally -- walk outer helpers, then
            emit Poll::ready / StopIteration as the coro's exit.
          * Active: forward -- move this exit's parked value into the
            outer's slot, set the outer's flag, walk helpers down to
            the outer boundary, transition to the outer's
            `finally_entry_bb`. The outer's `AsyncFinallyExit`
            eventually performs the local replay (or forwards again
            for deeper nesting)."""
        f = stmt.captured_exc_field
        inner = indent + INDENT
        if stmt.pending_return_flag is not None:
            flag = stmt.pending_return_flag
            out.write(f"{indent}if (this->{flag}) {{\n")
            out.write(f"{inner}this->{flag} = false;\n")
            # Swallow any in-flight exception captured by the try
            # region's catch: Python semantics say return-in-finally
            # wins, including over a propagating raise. For a return
            # from the try body (no in-flight exception), this is a
            # no-op.
            out.write(f"{inner}this->{f} = nullptr;\n")
            # If an enclosing CFG-based finally is active (set in ctx
            # by `_emit_case` from this BB's region_stack), forward the
            # parked state into the outer's slots and transition to its
            # finally_entry instead of replaying locally -- otherwise
            # the outer finally body would be skipped.
            outer_flag = self.ctx.async_pending_return_flag
            if outer_flag is not None:
                outer_slot = self.ctx.async_pending_return_slot
                outer_target = self.ctx.async_pending_return_target_state
                outer_boundary = self.ctx.async_pending_return_boundary
                inner_slot = stmt.pending_return_slot
                if outer_slot is not None and inner_slot is not None:
                    out.write(f"{inner}this->{outer_slot} = "
                              f"std::move(this->{inner_slot});\n")
                out.write(f"{inner}this->{outer_flag} = true;\n")
                # Walk helpers between this exit and the outer finally
                # entry; helpers BELOW the outer (`outer_boundary`) stay
                # on the stack to run inside the outer's finally region.
                emit_prims.emit_finally_chain(self.ctx, out, inner,
                                              stop_at=outer_boundary)
                out.write(f"{inner}__state = {outer_target};\n")
                out.write(f"{inner}continue;\n")
            else:
                # No outer CFG finally: replay locally. Walk any outer
                # helpers (an enclosing helper-based finally outside
                # this CFG-based one) before emitting the actual return.
                emit_prims.emit_finally_chain(self.ctx, out, inner)
                if self._is_generator_shape():
                    done_state = self.ctx.generator_resumable_done_state or "S_DONE"
                    out.write(f"{inner}__state = {done_state};\n")
                    out.write(f"{inner}return ::tpy::make_unexpected("
                              f"::tpy::StopIteration{{}});\n")
                else:
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
        out.write(f"{indent}if (this->{f}) {{\n")
        out.write(f"{inner}std::exception_ptr __tmp = this->{f};\n")
        out.write(f"{inner}this->{f} = nullptr;\n")
        out.write(f"{inner}std::rethrow_exception(__tmp);\n")
        out.write(f"{indent}}}\n")

    def _emit_with_enter(self, out: "TextIO", indent: str,
                                stmt: 'rcfg.WithEnter') -> None:
        """Emit the with-stmt setup sequence (manager bind via
        `_emit_with_ctx_bind`, then the `__enter__` call):
            <target> = (*__with_ctx_<n>).__enter__();   # if target
            (*__with_ctx_<n>).__enter__();              # else
        """
        ctx_n = stmt.ctx_n
        item = stmt.item
        self._emit_with_ctx_bind(out, indent, ctx_n, item)
        if item.target is not None:
            target = escape_cpp_name(item.target)
            enter_call = f"(*__with_ctx_{ctx_n}).__enter__()"
            if item.target in self.ctx.generator_frame_slot_locals:
                # A non-value `as`-target hoisted across a yield is backed by
                # `tpy::frame_slot<T>` (deleted operator=); construct via
                # emplace, mirroring the var-decl / reassign frame-slot paths.
                # Pointer-form Optional targets stay `T*` and bind with `=`.
                out.write(f"{indent}{target}.emplace({enter_call});\n")
            else:
                out.write(f"{indent}{target} = {enter_call};\n")
        else:
            out.write(f"{indent}(*__with_ctx_{ctx_n}).__enter__();\n")

    def _emit_async_with_setup(self, out: "TextIO", indent: str,
                                 stmt: 'rcfg.AsyncWithSetup') -> None:
        """Populate the async-with `__with_ctx_<n>` frame field. Subsequent
        Yield BBs (aenter/aexit) emplace `__sub_<i>` with
        `(*__with_ctx_<n>, ...)`."""
        self._emit_with_ctx_bind(out, indent, stmt.ctx_n, stmt.item)

    def _emit_with_ctx_bind(self, out: "TextIO", indent: str, ctx_n: int,
                            item: TpyWithItem) -> None:
        """Populate the `__with_ctx_<n>` frame field for a (sync or async)
        with-region: emplace an owned `frame_slot<CM>` manager (whose
        `operator=` is deleted), or bind a borrowed `CM*`. A global manager
        already renders as `CM*`, so the borrowed bind must not re-take its
        address (would double-pointer)."""
        ctx_expr = self._leaf.render_region_expr(item.context_expr)
        self.ctx.temps.flush(out, indent)
        if item.manager_borrowed:
            if self.ctx.is_already_pointer_source(item.context_expr):
                out.write(f"{indent}__with_ctx_{ctx_n} = {ctx_expr};\n")
            else:
                out.write(f"{indent}__with_ctx_{ctx_n} = &({ctx_expr});\n")
        else:
            out.write(f"{indent}__with_ctx_{ctx_n}.emplace({ctx_expr});\n")

    def _emit_with_exit(self, out: "TextIO", indent: str,
                               region: 'rcfg.WithRegion',
                               on_exception: bool,
                               exc_val_cpp: str | None = None) -> None:
        """Emit a single `__exit__` call. `on_exception=True` passes the
        catch-bound `__exc_<n>` (or `nullptr` when sema marked
        exit_takes_exc_val=False); otherwise passes the "normal exit"
        args (all empty / nullptr). `exc_val_cpp` overrides the exc_val
        argument when the manager takes one -- the frame destructor uses
        it to pass the GeneratorExit it constructed for the close."""
        item = region.item
        n = region.ctx_n
        if item.exit_takes_exc_val and exc_val_cpp is not None:
            exc_arg = exc_val_cpp
        elif on_exception and item.exit_takes_exc_val:
            exc_arg = f"&__exc_{n}"
        elif item.exit_takes_exc_val:
            exc_arg = "nullptr"
        else:
            exc_arg = "{}"
        out.write(f"{indent}(*__with_ctx_{n}).__exit__("
                  f"{{}}, {exc_arg}, {{}});\n")

    def _for_info(self, func: TpyFunction, uid: int) -> 'GeneratorForInfo | None':
        return rcfg.resumable_state(func).for_info_by_uid.get(uid)

    def _for_src_access(self, out: "TextIO", indent: str,
                        iterable_expr: 'TpyExpr', uid: int,
                        info: 'GeneratorForInfo') -> str:
        """Resolve the for-loop source expression for the begin_end / next
        strategies. When the iterable is a temporary (the pre-scan allocated
        `__for_src_<uid>`), store it once here and return the stored access;
        otherwise return the (re-evaluable) named expression."""
        # The leaf hands over a BARE render for every narrowed-optional
        # iterable it admits, so this unwrap is the single owner of the
        # narrowed-Optional / indirect-name deref.
        base_cpp = self._leaf.render_region_expr(iterable_expr)
        src_cpp = emit_prims.maybe_unwrap_narrowed_optional(
            self.ctx, iterable_expr, base_cpp,
            self.ctx.is_indirect_name(iterable_expr))
        self.ctx.temps.flush(out, indent)
        if any(fn == f"__for_src_{uid}" for fn, _ in info.fields):
            out.write(f"{indent}__for_src_{uid}.emplace({src_cpp});\n")
            return f"(*__for_src_{uid})"
        return src_cpp

    def _emit_async_for_iter_setup(self, out: "TextIO", indent: str,
                                    stmt: 'rcfg.AsyncForIterSetup',
                                    func: TpyFunction) -> None:
        """Initialize for-loop iteration state into the frame, per strategy
        (mirrors the simple-generator peephole init so range / begin_end
        generators keep their fast shape):
          async_for: `__for_itr.emplace((it).__aiter__())`
          range:     `__for_i`/`__for_stop`[/`__for_step`] counters
          begin_end: `[__for_src.emplace(...);] __for_it.emplace(src.begin()); __for_end.emplace(...end())`
          next:      `[__for_src.emplace(...);]` (the source IS the iterator)
          iter_next: `::tpy::resumable_iter_init(__for_itr, src)` (owns the
                     derived iterator, or no-ops for a self-iterator source)
        """
        uid = stmt.uid
        if stmt.is_async:
            iter_cpp = self._leaf.render_region_expr(stmt.iterable_expr)
            self.ctx.temps.flush(out, indent)
            # A global iterable already renders as `Src*`; deref so the
            # `.__aiter__()` call resolves rather than hitting `.`-on-pointer.
            if self.ctx.is_already_pointer_source(stmt.iterable_expr):
                iter_cpp = f"*({iter_cpp})"
            out.write(f"{indent}__for_itr_{uid}.emplace(({iter_cpp}).__aiter__());\n")
            return
        info = self._for_info(func, uid)
        strat = info.strategy if info else "iter_next"
        if strat == "range":
            self._emit_for_range_setup(out, indent, stmt, uid)
        elif strat == "begin_end":
            src = self._for_src_access(out, indent, stmt.iterable_expr, uid, info)
            out.write(f"{indent}__for_it_{uid}.emplace(({src}).begin());\n")
            out.write(f"{indent}__for_end_{uid}.emplace(({src}).end());\n")
        elif strat == "next":
            # Source IS the iterator; just stash a temporary if needed.
            self._for_src_access(out, indent, stmt.iterable_expr, uid, info)
        else:  # iter_next (universal)
            # A temporary source is stashed in `__for_src` first because
            # `tpy::__iter__` borrows its argument (it would otherwise dangle).
            # The init/next go through resumable_iter_* so a self-iterator
            # source (a move-only generator) is driven in place instead of
            # copied into the iterator slot -- see generator.hpp.
            src = self._for_src_access(out, indent, stmt.iterable_expr, uid,
                                       info)
            out.write(f"{indent}::tpy::resumable_iter_init(__for_itr_{uid}, {src});\n")

    def _emit_for_range_setup(self, out: "TextIO", indent: str,
                              stmt: 'rcfg.AsyncForIterSetup', uid: int) -> None:
        """range() counter init into the frame counters."""
        range_call = stmt.iterable_expr
        elem_type = stmt.elem_type
        if elem_type and isinstance(elem_type, IntLiteralType):
            elem_type = self.ctx.analyzer.ctx.default_int_type
        cpp_elem = self.types.type_to_cpp(elem_type) if elem_type else "int32_t"
        gen_args = [self._leaf.render_region_expr(a)
                    for a in range_call.args]
        self.ctx.temps.flush(out, indent)
        nargs = len(gen_args)
        ci, st = f"__for_i_{uid}", f"__for_stop_{uid}"
        if nargs == 1:
            out.write(f"{indent}{ci}.emplace({cpp_elem}(0));\n")
            out.write(f"{indent}{st}.emplace(static_cast<{cpp_elem}>({gen_args[0]}));\n")
        else:
            out.write(f"{indent}{ci}.emplace(static_cast<{cpp_elem}>({gen_args[0]}));\n")
            out.write(f"{indent}{st}.emplace(static_cast<{cpp_elem}>({gen_args[1]}));\n")
        if nargs == 3:
            sp = f"__for_step_{uid}"
            out.write(f"{indent}{sp}.emplace(static_cast<{cpp_elem}>({gen_args[2]}));\n")
            if emit_prims.extract_int_literal(range_call.args[2]) is None:
                out.write(f"{indent}::tpy::range_check_step_nonzero(*{sp});\n")
            if not is_big_int_type(elem_type):
                emit_prims.gen_range_overflow_check(
                    out, indent, f"*{ci}", f"*{st}", f"*{sp}", elem_type)

    def _for_advance_parts(self, func: TpyFunction,
                           t: 'rcfg.AsyncForAdvance') -> tuple[str, str, list[str]]:
        """Return (pre, exhausted_test, bind_post) for the AsyncForAdvance,
        per strategy. `pre` runs before the exhaustion test; the loop exits
        when `exhausted_test` is true; `bind_post` is the list of statements
        that bind the loop var (and advance the cursor) on the live path."""
        uid = t.uid
        stmt = t.stmt
        info = self._for_info(func, uid)
        strat = info.strategy if info else "iter_next"
        cpp_var = escape_cpp_name(stmt.var)
        if strat == "range":
            ci, st = f"(*__for_i_{uid})", f"(*__for_stop_{uid})"
            range_call = stmt.iterable
            nargs = len(range_call.args)
            if nargs == 3:
                sp = f"(*__for_step_{uid})"
                step_lit = emit_prims.extract_int_literal(range_call.args[2])
                if step_lit is not None and step_lit > 0:
                    cont = f"{ci} < {st}"
                elif step_lit is not None and step_lit < 0:
                    cont = f"{ci} > {st}"
                else:
                    cont = f"({sp} > 0 ? {ci} < {st} : {ci} > {st})"
                bind_post = [f"{cpp_var} = {ci};", f"{ci} += {sp};"]
            else:
                cont = f"{ci} < {st}"
                bind_post = [f"{cpp_var} = ({ci})++;"]
            return ("", f"!({cont})", bind_post)
        if strat == "begin_end":
            it, end = f"(*__for_it_{uid})", f"(*__for_end_{uid})"
            if info.borrow_tuple_loop_var == stmt.var:
                # Proxy-ref tuple element (dict_items): lower the prvalue
                # proxy to the borrow-form tuple; element refs are stable.
                elem_bare = unwrap_readonly(unwrap_ref_type(
                    self.types.resolve_type(stmt.elem_type)))
                borrow_cpp = self.types.tuple_borrow_cpp(elem_bare)
                bind_post = [
                    f"{cpp_var} = ::tpy::tuple_to_pointer"
                    f"<{borrow_cpp}>(*({it})++);"]
            elif info.pointer_form_loop_var == stmt.var:
                bind_post = [f"{cpp_var} = &(*({it})++);"]
            else:
                bind_post = [f"{cpp_var} = *({it})++;"]
            return ("", f"{it} == {end}", bind_post)
        # next / iter_next: __next__() into __for_r, exhaust on !has_value.
        r = f"(*__for_r_{uid})"
        if strat == "next":
            src = self._for_src_expr(stmt, uid, info)
            pre = f"__for_r_{uid}.emplace({src}.__next__());"
        else:  # iter_next
            # resumable_iter_next drives the owned iterator, or the self-iterator
            # source in place -- both re-read `src` so a frame move never dangles.
            src = self._for_src_expr(stmt, uid, info)
            pre = (f"__for_r_{uid}.emplace("
                   f"::tpy::resumable_iter_next(__for_itr_{uid}, {src}));")
        # A source-derived field takes one spelling whichever form the trait
        # picked: `unwrap_ref_move` hands `frame_slot` either a reference to bind
        # or a value to move in, and `emplace` resolves to the right one.
        if info is not None and info.loop_var_field is not None \
                and info.loop_var_field[0] == stmt.var:
            return (pre, f"!{r}.has_value()",
                    [f"{cpp_var}.emplace(::tpy::unwrap_ref_move(*{r}));"])
        elem = f"::tpy::unwrap_ref(*{r})"
        # Bind form mirrors the loop var's frame storage shape:
        # pointer-form `T*` (alias), frame_slot `.emplace`, or value assign.
        if info is not None and info.pointer_form_loop_var == stmt.var:
            bind_post = [f"{cpp_var} = &({elem});"]
        elif stmt.var in self.ctx.generator_frame_slot_locals:
            bind_post = [f"{cpp_var}.emplace({elem});"]
        else:
            bind_post = [f"{cpp_var} = {elem};"]
        return (pre, f"!{r}.has_value()", bind_post)

    def _for_src_expr(self, stmt: 'TpyForEach', uid: int,
                      info: 'GeneratorForInfo') -> str:
        """The iterator source for the `next` strategy advance: the stored
        `__for_src` (temporary) or the re-referencable named expression."""
        if any(fn == f"__for_src_{uid}" for fn, _ in info.fields):
            return f"(*__for_src_{uid})"
        return self._leaf.render_region_expr(stmt.iterable)

    def _emit_async_for_advance(self, out: "TextIO", indent: str,
                                  cfg: 'rcfg.CFG',
                                  t: 'rcfg.AsyncForAdvance',
                                  case_entries: dict[int, _StateLabel],
                                  func: TpyFunction,
                                  from_bb: int) -> None:
        """Emit the per-iteration advance for an AsyncForAdvance terminator
        (strategy-specific check + bind), then transfer to the body
        (`has_value_bb`) or loop exit (`exhausted_bb`)."""
        pre, exhausted_test, bind_post = self._for_advance_parts(func, t)
        if pre:
            out.write(f"{indent}{pre}\n")
        out.write(f"{indent}if ({exhausted_test}) {{\n")
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
        # has_value path -- bind loop var (and advance the cursor) then body.
        for line in bind_post:
            out.write(f"{indent}{line}\n")
        self._walk_inline_or_jump(
            out, cfg, t.has_value_bb, case_entries, func, from_bb=from_bb)

    def _emit_unreachable_tail(self, out: "TextIO", indent: str,
                                 func: TpyFunction) -> None:
        """Tail emission for a BB whose end is statically unreachable
        (no explicit return/raise). Generators that fall off the end stop
        iterating (StopIteration). For void async defs, emit Ready(unit);
        otherwise panic."""
        if self._is_generator_shape():
            # Fell off the end of the generator body -> StopIteration.
            emit_prims.emit_finally_chain(self.ctx, out, indent)
            out.write(f"{indent}__state = S_DONE;\n")
            out.write(f"{indent}return ::tpy::make_unexpected("
                      f"::tpy::StopIteration{{}});\n")
            return
        if self._is_void_return(func):
            # Walk any active finally frames before returning.
            emit_prims.emit_finally_chain(self.ctx, out, indent)
            out.write(f"{indent}__state = S_DONE;\n")
            out.write(f"{indent}{POLL_VOID_READY_RETURN}\n")
        else:
            out.write(f"{indent}::tpy::tpy_panic(\"async def fell "
                      f"through without returning a value\");\n")

    @staticmethod
    def _sub_field_name(suspension_index: int) -> str:
        return f"__sub_{suspension_index}"

    @staticmethod
    def _sub_slot_name(payload: 'rcfg.AwaitPayload',
                       suspension_index: int) -> str:
        """The C++ slot a suspension polls/resets: the dedicated
        `__sub_<i>` field, or -- for a prebuilt-slot await of a bound
        coroutine -- the handle's own frame field."""
        if payload.prebuilt_slot is not None:
            return escape_cpp_name(payload.prebuilt_slot)
        return AsyncCoroCodegen._sub_field_name(suspension_index)

    def _emplace_args(self, call: 'TpyCall | TpyMethodCall') -> 'list[str]':
        """All emplace args for a sub-coro construction -- the leaf seam's
        argument chokepoint."""
        return self._leaf.render_await_args(call)

    def _suspend_expr_cpp(self, expr: 'TpyExpr') -> str:
        """Render a bound-method await receiver or an ERASED/BORROWED await
        operand -- the THIR leaf seam's suspend-expression chokepoint (the
        skeleton keeps its move / & / .get() / __self-prepend wrap around
        this render)."""
        return self._leaf.render_suspend_expr(expr)

    def _emit_sub_reset(self, out: "TextIO", indent: str,
                        payload: 'rcfg.AwaitPayload',
                        suspension_index: int) -> None:
        sub = self._sub_slot_name(payload, suspension_index)
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
        sub = self._sub_slot_name(payload, suspension_index)
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
            # This bind ASSIGNS a frame field rather than DECLARING a local, so
            # it never reaches the var-decl promotion -- register the name
            # here or the working set under-covers every await-bound local and
            # its last use copies (an uncompilable copy for a @nocopy payload).
            emit_prims.promote_movable(self.ctx, payload.bind_target)
            if payload.bind_target in self.ctx.generator_optional_fields:
                out.write(f"{indent}{target}.emplace({moved});\n")
            else:
                out.write(f"{indent}{target} = {moved};\n")
        elif payload.kind is rcfg.AwaitKind.RETURN:
            # `return await h.borrow()` at an OWNING return: the sub's
            # payload is a `T*` into the awaitee's storage and the slot is a
            # `T` by value, so the forward copy-constructs through the deref
            # -- the same copy `copy(await ...)` spells, which sema warned
            # about at this return. Every other payload forwards as-is.
            if (payload.await_node.await_result_is_borrow
                    and async_return_form(func.return_type)
                    is AsyncReturnForm.STORAGE
                    and not self._is_void_return(func)):
                _fwd_cpp = self._ret_cpp(func)
                out.write(f"{indent}{_fwd_cpp} __ret{suspension_index} = "
                          f"{_fwd_cpp}(*({moved}));\n")
            else:
                out.write(f"{indent}auto __ret{suspension_index} = "
                          f"{moved};\n")
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
                terminated = emit_prims.emit_finally_chain(
                    self.ctx, out, indent, stop_at=boundary)
                if not terminated:
                    out.write(f"{indent}__state = {target_state};\n")
                    out.write(f"{indent}continue;\n")
                return
            emit_prims.emit_finally_chain(self.ctx, out, indent)
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

    def _emit_yield_terminator(self, out: "TextIO", indent: str,
                               t: 'rcfg.Yield', func: TpyFunction) -> None:
        """Emit the full suspend action for a Yield terminator (policy
        seam). Async stores the sub-future + advances state, then
        `continue`s to re-enter the switch at the new state -- the resume
        case's poll() returns Pending iff the sub-future is genuinely
        not-yet-ready; continuing (rather than returning Pending
        unconditionally) lets a synchronously-ready sub-future complete
        in one poll. The generator shape emits the yielded value +
        advances state + returns the value to the caller (the next
        `__next__()` call resumes at the new state)."""
        if self._is_generator_shape():
            self._emit_generator_yield(out, indent, t)
            return
        self._emit_suspend(out, indent, t.payload, t.suspension_index, func)
        out.write(f"{indent}continue;\n")

    def _emit_generator_yield(self, out: "TextIO", indent: str,
                              t: 'rcfg.Yield') -> None:
        """Generator suspension: compute the yielded value, advance to the
        resume state, and return it to the caller. The next `__next__()`
        call re-enters the switch at the resume state, whose case header
        is a no-op (no poll/bind) and simply continues after the yield."""
        payload = t.payload
        assert isinstance(payload, rcfg.YieldPayload), \
            "generator shape requires a YieldPayload terminator"
        ys = payload.yield_stmt
        assert ys is not None, \
            "YieldPayload built from a generator body always carries yield_stmt"
        if ys.loc is not None:
            self.ctx.emit_source_comment(out, ys.loc, indent)
        yield_expr = self._leaf.render_yield_value(ys)
        self.ctx.temps.flush(out, indent)
        resume = _StateLabel(_StateKind.RESUME, t.suspension_index).cpp_name()
        out.write(f"{indent}__state = {resume};\n")
        out.write(f"{indent}return {yield_expr};\n")

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
        sub = self._sub_slot_name(payload, suspension_index)
        if payload.prebuilt_slot is not None:
            # Bound-coroutine await: the handle's frame slot is already
            # engaged (emplaced at the binding); nothing to construct.
            pass
        elif payload.mode is rcfg.AwaitMode.INLINE:
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
                    args = self._emplace_args(call)
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}{sub}.emplace({', '.join(args)});\n")
                elif isinstance(call, TpyMethodCall):
                    is_module_call = (call.user_module_call is not None
                                      or call.builtin_module_call is not None)
                    if is_module_call:
                        # `module.func(...)` -- receiver is a namespace,
                        # not a value, so the sub-coro ctor takes only
                        # the function args.
                        args = self._emplace_args(call)
                        self.ctx.temps.flush(out, indent)
                        out.write(f"{indent}{sub}.emplace({', '.join(args)});\n")
                    else:
                        # Bound async method: prepend receiver as __self ctor arg.
                        recv_cpp = self._suspend_expr_cpp(call.obj)
                        arg_cpps = self._emplace_args(call)
                        self.ctx.temps.flush(out, indent)
                        joined = ", ".join([recv_cpp] + arg_cpps)
                        out.write(f"{indent}{sub}.emplace({joined});\n")
                else:
                    raise CodeGenError(
                        "internal: inline-mode await operand is not a call",
                        loc=None)
        elif payload.mode is rcfg.AwaitMode.ERASED:
            operand_cpp = self._suspend_expr_cpp(payload.operand_expr)
            self.ctx.temps.flush(out, indent)
            out.write(f"{indent}{sub}.emplace(std::move({operand_cpp}));\n")
        elif payload.mode is rcfg.AwaitMode.BORROWED:
            operand_cpp = self._suspend_expr_cpp(payload.operand_expr)
            self.ctx.temps.flush(out, indent)
            declared = emit_prims.cpp_declared_type(
                self.ctx, payload.operand_expr)
            declared = unwrap_readonly(unwrap_send_sync(declared)) if declared else None
            if (isinstance(declared, OwnType)
                    and is_dyn_protocol(unwrap_readonly(declared.wrapped))):
                # Owned-erased handle (unique_ptr<P> local/param): the
                # sub-future points at the heap payload; polls dispatch
                # through the protocol vtable.
                out.write(f"{indent}{sub} = {operand_cpp}.get();\n")
            # A global / pointer-alias loop var already renders as `T*`;
            # re-taking its address would double-pointer the sub-future field.
            elif self.ctx.is_already_pointer_source(payload.operand_expr):
                out.write(f"{indent}{sub} = {operand_cpp};\n")
            else:
                out.write(f"{indent}{sub} = &({operand_cpp});\n")
        else:
            raise CodeGenError(f"unknown await mode {payload.mode!r}",
                               loc=None)
        out.write(f"{indent}__state = "
                  f"{_StateLabel(_StateKind.RESUME, suspension_index).cpp_name()};\n")


def sub_struct_qualname(
        types, owner: 'NominalType | None', method: str,
        inferred_type_args: 'tuple[TpyType, ...] | None' = None,
        *, module_qual: str | None = None,
        extra_template_args: 'list[str] | None' = None,
        loc=None) -> str:
    """The sub-coro struct name for a statically-resolved await -- the
    async face of `rcfg.frame_struct_qualname` (which owns the naming
    grammar; see its docstring). Also the renderer for ConcreteCoroType (a
    bound coroutine's frame struct), which needs the same naming from the
    type-to-C++ path where no AsyncCoroCodegen instance exists.
    """
    return rcfg.frame_struct_qualname(
        types, owner, method, inferred_type_args,
        module_qual=module_qual, extra_template_args=extra_template_args,
        shape=rcfg.ResumableShape.ASYNC, loc=loc)
