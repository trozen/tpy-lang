"""Code generation for `async def` functions (state-machine struct conforming
to Awaitable[T]).

The printer of the resumable frame THIR publishes, with the shape from
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
import io
from dataclasses import dataclass, field
from enum import IntEnum
from functools import partial
from typing import TYPE_CHECKING

from ..identity_map import IdentityMap
from ..namespace import Namespace
from ..parse.nodes import (
    SourceLocation, read_names,
    TpyFunction, TpyStmt,
    TpyName, TpyExpr, TpyCall,
    TpyMethodCall, TpyForEach, TpyWith, TpyWithItem,
    TpyCoerce,
    TpyArrayLiteral, TpyDictLiteral, TpySetLiteral, TpyListRepeat,
    TpyListComprehension, TpyDictComprehension, TpySetComprehension,
    walrus_bindings,
)


# Collection literals / comprehensions have no concrete C++ type at a
# protocol-typed call site (the param is a concept) and no frame storage to
# survive a suspension, so they can't back an inline-await sub-future field.
_FRESH_COLLECTION_NODES = (
    TpyArrayLiteral, TpyDictLiteral, TpySetLiteral, TpyListRepeat,
    TpyListComprehension, TpyDictComprehension, TpySetComprehension,
)
from ..typesys import (ResultPosition, ResultRepresentation,
                       classify_result_representation, pointer_repr_optional,
                       yield_borrow_slot_cpp)
from ..typesys import IntLiteralType, NominalType, TpyType, OptionalType, ReadonlyType, TupleType, unwrap_readonly, unwrap_ref_type, unwrap_own, unwrap_send_sync, VoidType, is_fn_type, ConcreteFrameType
from ..value_category import (async_return_form, AsyncReturnForm,
                              for_source_is_rvalue)
from ..type_def_registry import (is_str_type, is_big_int_type,
                                  view_owned_copy_init)
from . import emit_prims
from .context import (INDENT, escape_cpp_name, CodeGenError, FinallyContext)
from .protocols import protocol_param_template_name, fn_param_template_name, signature_is_template
from .functions import default_to_cpp, default_emittable
from ..resumable_shape import ResumableShape
from ..thir.frame import (
    AsyncWithKind, AwaitKind, AwaitMode, FrameCaptureKind, FrameLocalKind,
    FrameLocalLayout, FrameLoopOwner, GeneratorForInfo,
    THIRAsyncWithSetup, THIRAwaitSuspension, THIRBranch,
    THIRExceptRegion, THIRFall, THIRFinallyExit, THIRFinallyRegion,
    THIRForAdvance, THIRForIterSetup, THIRFrameRaise, THIRFrameReturn,
    THIRLoopSourceRegion, THIRMatchDispatch, THIRRegion, THIRResumableFrame,
    THIRSuspend, THIRTryRegion, THIRUnreachable,
    THIRWithEnter, THIRWithRegion, THIRYieldSuspension,
)
from ..thir.lower.frame import (
    FrameBuildDeps, build_resumable_frame, frame_function_info,
)
from ..thir.frame import region_cleanups
from .frame_names import frame_struct_name, sub_struct_qualname
from .frame_fields import FrameField, order_frame_fields


_OWNING_FRAME_KINDS = frozenset((
    FrameLocalKind.VALUE, FrameLocalKind.OWNED_STR,
    FrameLocalKind.FRAME_SLOT, FrameLocalKind.OWNING_TUPLE_SLOT))




def _regions_have_pending_cleanup(regions: tuple) -> bool:
    """Return True if any region in the tuple will emit cleanup code.

    Used by _emit_exit_region_finallies to decide whether to defer the
    __finally_stop check: if the destination region stack still has pending
    cleanup (with.__exit__ or finally helpers), the stop-check must wait
    until those later states have run their cleanup."""
    for r in regions:
        if isinstance(r, THIRWithRegion):
            return True
        if isinstance(r, THIRFinallyRegion):
            # BBs inside a CFG-based finally body: the finally body itself
            # is the pending cleanup; don't stop before it runs.
            return True
        if isinstance(r, THIRTryRegion) and r.finally_helper_name is not None:
            return True
        if isinstance(r, THIRExceptRegion) and r.parent_finally is not None:
            return True
    return False


if TYPE_CHECKING:
    from collections.abc import Callable
    from io import TextIO
    from ..typesys import TpyType
    from .context import CodeGenContext
    from .types import TypeMapper
    from .functions import FunctionGenerator
    from ..thir.frame import THIRLeafSlot
    from ..parse.nodes import TpyMatchCase
    from ..thir.nodes import THIRResumableReturn

    # Saved (narrowed_vars, protocol_narrowings, literal_facts) snapshots that
    # _restore_resume_narrowings reverts after a narrowed scope is emitted.
    _NarrowingToken = tuple[dict[str, str | None],
                            dict[str, TpyType], dict[str, TpyType]]







# The kinds whose C++ type is a deduced template arg taken by forwarding
# reference (`X&&` in the ctor and the factory, `std::forward` into the field).
_DEDUCED_SLOT_KINDS = (FrameCaptureKind.STATIC_PROTOCOL, FrameCaptureKind.FN,
                       FrameCaptureKind.CAPTURE)
# The kinds the caller hands over by ownership transfer: the ctor takes `&&`
# and moves into the field, the factory forwards with `std::move`.
_MOVED_IN_KINDS = (FrameCaptureKind.OWNED_VALUE, FrameCaptureKind.OWNED_TUPLE)


@dataclass(frozen=True)
class _CoroParam:
    cpp_name: str
    field_type: str  # type spelling for the frame-field declaration
    ctor_param_type: str  # type spelling for the constructor's parameter
    kind: FrameCaptureKind
    # OWNED_COPY only: the ctor-init RHS expression copying the borrow param into
    # the owned field (`::tpy::Bytes(b_)`, or the optional-aware form for
    # `str|None` / `bytes|None`). Built via view_owned_copy_init.
    owned_copy_init: str = ""

    def field_decl(self) -> str:
        if self.kind is FrameCaptureKind.REF:
            return f"{self.field_type}& {self.cpp_name}"
        return f"{self.field_type} {self.cpp_name}"

    def factory_param_decl(self) -> str:
        # Factory function signature param: same C++ type as the ctor's
        # param (matters for TYPE_PARAM, where it's `param_val_or_ref_t<T>`
        # rather than the field's `val_or_ref_t<T>`), but with the bare
        # name -- the factory body forwards by bare name.
        if self.kind is FrameCaptureKind.REF:
            return f"{self.ctor_param_type}& {self.cpp_name}"
        if self.kind in _DEDUCED_SLOT_KINDS:
            return f"{self.ctor_param_type}&& {self.cpp_name}"
        if self.kind in _MOVED_IN_KINDS:
            return f"{self.ctor_param_type} {self.cpp_name}"
        # OWNED_COPY: factory takes the borrow form by value (bare name), same
        # as VALUE; the ctor copies it into the owned field.
        return f"{self.ctor_param_type} {self.cpp_name}"

    def ctor_param_decl(self) -> str:
        if self.kind is FrameCaptureKind.REF:
            return f"{self.ctor_param_type}& {self.cpp_name}"
        if self.kind in _DEDUCED_SLOT_KINDS:
            return f"{self.ctor_param_type}&& {self.cpp_name}_"
        if self.kind in _MOVED_IN_KINDS:
            return f"{self.ctor_param_type}&& {self.cpp_name}_"
        # VALUE / POINTER / TYPE_PARAM / OWNED_COPY: `_` suffix disambiguates
        # from the field name in the init list.
        return f"{self.ctor_param_type} {self.cpp_name}_"

    def ctor_init(self) -> str:
        if self.kind is FrameCaptureKind.REF:
            return f"{self.cpp_name}({self.cpp_name})"
        if self.kind in (FrameCaptureKind.POINTER, FrameCaptureKind.TYPE_PARAM):
            # POINTER: raw pointer, trivially copyable -- std::move is noise.
            # TYPE_PARAM: `param_val_or_ref_t<T>` is already a reference type;
            # std::move on it yields an rvalue that won't bind to the field
            # type for non-value Ts. Direct bind/copy is uniformly correct.
            return f"{self.cpp_name}({self.cpp_name}_)"
        if self.kind in _DEDUCED_SLOT_KINDS:
            # `T_<pname>` / `F_<pname>` is the class template param, so the
            # ctor's `&&` reference-collapses (a plain lvalue ref when the
            # factory deduced a borrowed lvalue). Forward to bind both the
            # value and lvalue-ref cases; a bare std::move breaks the latter.
            return (f"{self.cpp_name}("
                    f"std::forward<{self.ctor_param_type}>({self.cpp_name}_))")
        if self.kind is FrameCaptureKind.OWNED_COPY:
            # Copy the borrow-form param into the owned field at construction,
            # so the value survives suspensions without aliasing the caller
            # (std::string(view) for str, ::tpy::Bytes(view) for bytes; the
            # nullable forms copy the inner only when present).
            return f"{self.cpp_name}({self.owned_copy_init})"
        return f"{self.cpp_name}(std::move({self.cpp_name}_))"


@dataclass(frozen=True)
class _ForSourceAlias:
    """One `using <name> = <definition>;` a frame struct declares for a `for`
    loop's source type, plus the names its definition reads (for the emit
    order: the render names a frame local by its field, and params and
    captures are declared before every local) and the loop's location (for
    the cycle reject)."""
    name: str
    definition: str
    reads: frozenset[str]
    loc: 'SourceLocation | None'


class _CodeLineCounter(io.StringIO):
    """A buffer that knows how many lines of code it holds: a size measure
    that does not depend on whether source echo comments are on."""

    @property
    def code_lines(self) -> int:
        return sum(1 for line in self.getvalue().splitlines()
                   if line.strip() and not line.lstrip().startswith("//"))


@dataclass
class _LoopForm:
    """How the state-transition sites render while a frame is emitted as ONE
    plain loop (`_single_loop_plan`): every resume point is the loop head, so
    a transfer to it is the loop's own `continue` and nothing stores a state
    on the way."""
    # The case labels that ARE the loop head: its own and every label whose
    # case would only re-dispatch to it.
    head_labels: frozenset[str]
    loop_label: str
    init_bb: int
    loop_bb: int
    # Exhaustion is recorded in `__state`, because asking the source again
    # is not free and harmless (anything but a begin/end pair or a counter).
    records_done: bool
    # The exhaustion test may become the loop's own condition.
    may_hoist_test: bool
    # Set by the advance emitter when it handed its test to the loop.
    condition: 'str | None' = None


def _state_jump(indent: str, label: str) -> str:
    """A re-dispatch to the case `label`."""
    return f"{indent}__state = {label};\n{indent}continue;\n"


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


@dataclass
class _FramePrint:
    """The printer's per-frame state: emit-order caches and the
    constructor's loop seed, which the body printer consults."""
    case_entries: 'dict[int, _StateLabel] | None' = None
    redispatch: 'dict[int, int | None]' = field(default_factory=dict)
    resume_to_yield: 'dict[int, THIRSuspend] | None' = None
    frame_fields: 'list[FrameField] | None' = None
    # The loop whose iterator setup the constructor already ran.
    ctor_seeded_uid: 'int | None' = None


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
        # Generators defined in the module being emitted, keyed
        # (name, owner_record): the frame builder resolves a same-module
        # delegated source through it. Filled per module before any emit.
        self.same_module_generators: dict[
            tuple[str, str | None], TpyFunction] = {}
        # Resumable-shape discriminator read by the `_resumable_*` policy
        # seams. ASYNC (await -> __poll__ -> Poll<T>) is the default; the
        # GENERATOR shape (yield -> __next__ -> expected<T, StopIteration>)
        # is selected transiently via `_resumable_shape`.
        self._shape: ResumableShape = ResumableShape.ASYNC
        # Set while a body is emitted as one plain loop (`_emit_single_loop`);
        # the state-transition sites read it.
        self._loop_form: '_LoopForm | None' = None

    @contextlib.contextmanager
    def _resumable_shape(self, shape: 'ResumableShape'):
        """Temporarily select the resumable shape (async / generator) the
        policy seams emit for, restoring the prior shape on exit."""
        prev = self._shape
        self._shape = shape
        try:
            yield
        finally:
            self._shape = prev

    def _is_generator_shape(self) -> bool:
        return self._shape is ResumableShape.GENERATOR

    def gen_struct_name(self, func: TpyFunction,
                        record_name: str | None = None) -> str:
        """Resumable-frame struct name for the CURRENT function. Shape-aware:
        `__gen_<funcname>` (/ `__gen_<Record>_<funcname>`) for the generator
        shape, `__coro_<funcname>` (/ `__coro_<Record>_<funcname>`) for the
        async shape -- so the struct name reflects what it is (the
        `operator<<` repr uses the same `__gen_` convention). The grammar
        itself lives in
        `frame_struct_name`, shared with every call site that embeds
        someone else's frame."""
        return frame_struct_name(func.name, record_name, self._shape)

    def _classify_params(self, func: TpyFunction,
                          record_name: str | None) -> list[_CoroParam]:
        """Classify async-def params for the coro struct field/ctor shape.

        Returns a list of `_CoroParam` records, one per captured param.
        When `record_name` is set, prepends `__self: <Record>&` (or
        `const <Record>&` for @readonly methods) so async methods
        capture their receiver -- parallels `GeneratorCodegen`
        self-capture.

        The capture kinds and const verdicts are the published frame's
        (`THIRResumableFrame.captures` / `const_params`); this only spells
        them.

        Generic params (`T` as TypeParamRef) use the
        `param_val_or_ref_t<T>` / `val_or_ref_t<T>` trait so each
        instantiation picks the right value-vs-reference shape -- a
        value-typed T (e.g. int32) stores by value (so literal /
        rvalue call-site args don't dangle), while an object-typed T
        stores by reference (matching Python semantics and the
        non-template ref path).

        str / bytes params are captured OWNED in the frame
        (FrameCaptureKind.OWNED_COPY -- the view is copied into std::string /
        tpy::bytes at construction) so they survive a suspension even when the
        arg is a temporary. Explicit view params (StrView/Span) stay borrow
        form (the caller opted into view semantics).
        """
        out: list[_CoroParam] = []
        frame = self.resumable_frame(func, record_name)
        if record_name:
            # The bare class name of a generic class in a type position
            # outside the class body is ill-formed: spell `Box<T>`.
            cpp_record = self.functions.protocols.record_cpp_qualifier(record_name)
            const_prefix = "const " if frame.receiver.readonly else ""
            recv_type = f"{const_prefix}{cpp_record}"
            out.append(_CoroParam(
                cpp_name="__self",
                field_type=recv_type,
                ctor_param_type=recv_type,
                kind=FrameCaptureKind.REF,
            ))
        for param, kind in zip(frame.params, frame.captures):
            pname, ptype = param.name, param.type
            const = pname in frame.const_params
            cpp_name = escape_cpp_name(pname)
            ptype_inner = unwrap_ref_type(ptype)
            actual = unwrap_readonly(ptype_inner)
            owned_init = ""
            if kind is FrameCaptureKind.VALUE:
                # Value types (incl. explicit views like StrView) store their
                # own C++ type by value; str/bytes are OWNED_COPY, not here.
                field_type = ctor_type = self.types.type_to_cpp(ptype_inner)
            elif kind is FrameCaptureKind.OWNED_COPY:
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
            elif kind is FrameCaptureKind.TYPE_PARAM:
                # to_cpp_return / to_cpp_param_type already encode the
                # val_or_ref_t<T> / param_val_or_ref_t<T> traits (and
                # collapse to std::size_t for INT-kind params).
                field_type = ptype_inner.to_cpp_return()
                ctor_type = ptype_inner.to_cpp_param_type()
            elif kind is FrameCaptureKind.STATIC_PROTOCOL:
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
            elif kind in (FrameCaptureKind.FN, FrameCaptureKind.CAPTURE):
                # Concrete callable type deduced as `F_<pname>` (declared in
                # `_emit_template_header`); stored by value, forwarded via
                # `F_<pname>&&` -- same shape as STATIC_PROTOCOL.
                field_type = ctor_type = fn_param_template_name(pname)
            elif kind in (FrameCaptureKind.POINTER, FrameCaptureKind.OWNED_TUPLE):
                if pointer_repr_optional(actual) is not None:
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
                    if spell is actual and const:
                        spell = ReadonlyType(actual)
                    field_type = ctor_type = spell.to_cpp_return()
                else:
                    # Non-value union: the pointer-variant borrow form
                    # (`::tpy::Union<A*, B*>`) is the shape every other param
                    # boundary uses -- ordinary functions, plain locals.
                    # Storing it by value in the frame keeps the
                    # factory's signature in step with what the call site (and
                    # the emplace path) already build; a value-variant-by-ref
                    # field would be the lone divergence. Deep-const when the
                    # param is `readonly[...]` OR the const verdict deep-consts
                    # it (matches the call site + body, addr-escape aware).
                    is_readonly_param = (actual is not ptype_inner) or const
                    field_type = ctor_type = (
                        self.types.type_to_cpp_const_ptr_variant(actual)
                        if is_readonly_param
                        else self.types.type_to_cpp_ptr_variant(actual))
            else:  # OWNED_VALUE / REF -- both spell the plain C++ type
                field_type = ctor_type = self.types.type_to_cpp(ptype_inner)
                # A reference capture is const when the param is explicitly
                # `readonly[T]` OR the inferred verdict const-borrows it --
                # the same pair the pointer arms above consult, and the same
                # verdict the sync twin's signature reads, so a caller holding
                # its own const borrow can drive the factory. OWNED_VALUE
                # (`Own[T]`) is excluded: it owns its payload, not a borrow.
                if kind is FrameCaptureKind.REF and const:
                    field_type = ctor_type = f"const {field_type}"
            out.append(_CoroParam(
                cpp_name=cpp_name,
                field_type=field_type,
                ctor_param_type=ctor_type,
                kind=kind,
                owned_copy_init=owned_init,
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
                if func.is_capture(pname)
                or is_fn_type(unwrap_readonly(unwrap_ref_type(ptype)))]

    def _is_templated_coro(self, func: TpyFunction,
                            record_name: str | None = None) -> bool:
        """True iff the coro/generator struct is a C++ template -- because
        of explicit `[T, ...]` type params, a static-protocol-typed param
        (`T_<pname>` template arg), a deduced-type slot (an `Fn` param, a
        genexpr's capture), OR (for a method) the enclosing record's type
        params. Templated structs must emit their poll/__next__ body +
        factory inline in the header: another module that reaches one
        instantiates it there. A non-template struct's body goes to the .cpp,
        except a small generator `__next__`, which
        `CodeGenerator._emit_generator_next` moves to `<mod>_inl.hpp` when the
        module emits one (`Compiler._module_emits_inl` decides that from the
        AST)."""
        return (bool(self.functions.protocols.record_template_args(record_name))
                or signature_is_template(func)
                or bool(self._fn_template_parts(func)))

    def _emit_template_header(self, out: "TextIO", func: TpyFunction,
                                *, indent: str = "",
                                record_name: str | None = None) -> bool:
        proto_parts = self._protocol_template_parts(func)
        fn_parts = self._fn_template_parts(func)
        record_parts = self.functions.protocols.record_template_parts(record_name)
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
        record_parts = self.functions.protocols.record_template_parts(record_name)
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
                  if p.kind is FrameCaptureKind.STATIC_PROTOCOL]
        extras += [p.field_type for p in cparams
                   if p.kind in (FrameCaptureKind.FN, FrameCaptureKind.CAPTURE)]
        all_args = (list(self.functions.protocols.record_template_args(record_name))
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
                                 ptype, func.params, func.is_method, func):
            return ""
        return f" = {default_to_cpp(self.ctx, defaults[param_index], ptype)}"

    def _emit_params_decl(self, func: TpyFunction, *,
                          emit_defaults: bool = False) -> str:
        # Defaults belong on the factory's forward declaration only -- the
        # definition would redefine them (C++ error). Aligned to func.params
        # (a free-function factory has no __self cparam).
        parts: list[str] = []
        for i, cp in enumerate(self._classify_params(func, None)):
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
        self.ctx.emit_declaration_echo(out, func.loc)
        return_type_name = self._struct_name_templated(func)
        self._emit_template_header(out, func)
        # Default arg values live on this forward decl (the canonical first
        # declaration); `gen_factory`'s definition omits them.
        params = self._emit_params_decl(func, emit_defaults=True)
        out.write(f"{return_type_name} {escape_cpp_name(func.name)}({params});\n")
        if self._builds_source_in_place(func):
            header, ret, in_place_params, _ = self._in_place_factory_parts(func)
            out.write(f"{header}\n{ret} {escape_cpp_name(func.name)}({in_place_params});\n")
        return True

    # -- Body partitioning ----------------------------------------------------

    # -- Borrowed-rvalue-arg lift (runs after the await-lift pass) ------------

    # -- Frame-local placement -------------------------------------------------

    def _for_source_aliases(self, func: TpyFunction, record_name: str | None,
                            cfg: 'THIRResumableFrame') -> 'list[_ForSourceAlias]':
        """The `using __for_src_<n>_t = ...;` each `for` loop of this frame
        spells its fields off: `decltype` of the loop's own source render,
        stripped to the owned value where the frame holds a fresh source.

        The render is the one the loop's setup emits, produced under the
        frame body scope so a frame local reads as its field and a capture
        as its slot -- and it is unevaluated: an argument temporary renders
        as `std::declval` of its type, since a class-scope `decltype` has no
        statement to declare it in. The names the render reads are recorded
        so the struct can order the alias after the fields it names.
        """
        state = self._frame_of(func)
        loops: 'list[tuple[TpyForEach, str, THIRLeafSlot | None, str | None]]' = []
        for s, info in state.for_loop_info.items():
            if info.src_alias is not None:
                loops.append((s, info.src_alias,
                              state.for_setup(info.uid).iterable, None))
        for s, held in state.for_src_fields.items():
            if held is not None:
                loops.append((s, held.cpp_type, None, held))
        if not loops:
            return []
        from ..thir.emit import UnevaluatedTemps
        leaf = self._thir_resumable_leaf_emitter(func, record_name, cfg)
        temps = UnevaluatedTemps(self.ctx)
        aliases: list[_ForSourceAlias] = []
        with self._resumable_frame_ctx(func, record_name):
            with self._thir_leaf_scope(leaf):
                for s, alias, iterable, held_field in loops:
                    cpp = leaf.render_for_source(iterable, temps=temps,
                                                 frame_src_field=held_field)
                    if (iterable is not None
                            and iterable in leaf.published.unwrapped_sources):
                        cpp = f"(*{cpp})"
                    rhs = f"decltype(({cpp}))"
                    if for_source_is_rvalue(s, self.ctx.analyzer):
                        rhs = f"::tpy::for_source_t<{rhs}>"
                    aliases.append(_ForSourceAlias(
                        alias, rhs,
                        # A walrus target the source binds is a field its
                        # render names too.
                        frozenset(read_names(s.iterable)) | frozenset(
                            w.target for w in walrus_bindings(s)),
                        s.loc))
        return aliases

    # Field kinds that point into other storage and own nothing.
    _BORROWING_FRAME_KINDS = frozenset((
        FrameLocalKind.PTR_ALIAS, FrameLocalKind.OPT_PTR,
        FrameLocalKind.REBIND_PTR, FrameLocalKind.BORROW_TUPLE))

    def _frame_fields(self, func: TpyFunction, record_name: str | None,
                      cfg: 'THIRResumableFrame') -> 'list[FrameField]':
        """Every field of this frame, in declaration order (see
        `frame_fields`). Cached on the frame's resumable state.

        The order they are handed to the sort is the frame's layout before
        any edge pulls: locals in declaration order (a loop var after the
        alias its field is spelled off), the loop source aliases, the loop,
        rebind-slot, `with` and `try` machinery, then the frame objects the
        body holds by value, then the sub-futures. A frame object goes that
        late because it borrows other fields and its destructor may run a
        pending `finally` over them; an edge only ever pulls it earlier, next
        to the loop whose source type is spelled off it."""
        state = self._frame_of(func)
        printed = self._print_state(state)
        if printed.frame_fields is not None:
            return printed.frame_fields
        locals_ = state.locals
        # A local no field can back is this frame's own located diagnostic;
        # it goes before the alias renders lower the body, whose reject for
        # the same local would otherwise be the one reported.
        for loc in locals_:
            lname = loc.name
            if loc.placement.kind is FrameLocalKind.PROTOCOL:
                raise CodeGenError(
                    f"local {lname!r} of protocol type aliasing a "
                    "protocol-typed parameter is only supported across a "
                    "suspension when bound exactly once directly from the "
                    "parameter; bind it once from the parameter, or "
                    "iterate the parameter directly",
                    loc=func.loc)
        aliases = self._for_source_aliases(func, record_name, cfg)
        # The aliases each loop var's field is spelled off -- every loop
        # that binds the name, since a shared field joins their payloads.
        aliases_of_local: dict[str, set[str]] = {}
        for info in state.for_loop_info.values():
            if info.src_alias is None:
                continue
            names = list(info.pointer_payloads)
            if info.loop_var_field is not None:
                names.append(info.loop_var_field[0])
            for name in names:
                aliases_of_local.setdefault(name, set()).add(info.src_alias)
        borrows, untraced = self._frame_local_borrows(func)

        def render(emit: 'Callable[[TextIO], None]') -> str:
            buf = io.StringIO()
            emit(buf)
            return buf.getvalue()

        local_fields: list[FrameField] = []
        frame_objects: list[FrameField] = []
        for loc in locals_:
            lname, ltype, verdict = loc.name, loc.type, loc.placement
            drops = (verdict.kind not in self._BORROWING_FRAME_KINDS
                     and verdict.kind is not FrameLocalKind.OWNED_STR
                     and unwrap_ref_type(ltype).drop_runs_user_code())
            ff = FrameField(
                name=lname,
                decl=render(partial(self._emit_frame_local_field,
                                    lname=lname, ltype=ltype,
                                    verdict=verdict, func=func)),
                borrows=frozenset(borrows.get(lname, ())),
                spelled_from=frozenset(aliases_of_local.get(lname, ())),
                drop_runs_user_code=drops,
                borrows_untraced=lname in untraced,
                local=lname, loc=func.loc)
            if self._holds_frame_by_value(ltype, verdict):
                frame_objects.append(ff)
            else:
                local_fields.append(ff)
        alias_fields = [
            FrameField(name=a.name,
                       decl=f"{INDENT}using {a.name} = {a.definition};\n",
                       spelled_from=a.reads, is_alias=True, loc=a.loc)
            for a in aliases]

        # Synthetic fields for for-loops: iterator, sentinel and step result
        # for a CFG-decomposed one, plus the source holder, which a loop that
        # keeps its block-local iterators also takes when it lends an element
        # out. frame_loop_slot has frame_slot's storage but unchecked reads:
        # only this loop's own emitted code reads them, after its init.
        loop_machinery: list[FrameField] = []
        for holder in state.for_fields:
            fname, ftype = holder.name, holder.cpp_type
            owner = holder.loop
            drops = holder.drop_runs_user_code
            loop_machinery.append(FrameField(
                name=fname,
                decl=f"{INDENT}::tpy::frame_loop_slot<{ftype}> {fname};\n",
                borrows=(self._loop_field_borrows(fname, owner)
                         if owner is not None else frozenset()),
                spelled_from=(frozenset([owner.alias])
                              if owner is not None and owner.alias is not None
                              else frozenset()),
                drop_runs_user_code=drops))

        # Per-write-site materialization slots for rvalue writes into
        # pointer-form frame locals (the frame builder's pointer-slot prescan). Each
        # follows the local it backs, so the frame destroys it in that
        # local's turn rather than after every other field.
        site_slots: dict[str, list[FrameField]] = {}
        for slot in state.ptr_slot_fields:
            target_loc = state.local(slot.local)
            target_t = target_loc.type if target_loc is not None else None
            if isinstance(target_t, OptionalType):
                target_t = target_t.inner
            drops = (target_t is None
                     or unwrap_ref_type(target_t).drop_runs_user_code())
            slot_field = FrameField(
                name=slot.name, decl=f"{INDENT}{slot.cpp_type} {slot.name};\n",
                borrows=frozenset(read_names(slot.value)),
                drop_runs_user_code=drops)
            if any(f.name == slot.local for f in local_fields):
                site_slots.setdefault(slot.local, []).append(slot_field)
            else:
                loop_machinery.append(slot_field)
        local_fields = [g for f in local_fields
                        for g in (f, *site_slots.get(f.name, ()))]

        # Context-manager fields for CFG-decomposed `with`-with-await
        # bodies. One per WithItem: an owning `frame_slot<T>` for a
        # by-value manager, or a `T*` borrow for a reference-type lvalue
        # manager (so __enter__/__exit__ act on the original, not a copy).
        with_items = self._with_field_items(func)
        for holder in state.with_fields:
            fname, ftype = holder.name, holder.cpp_type
            item = with_items.get(fname)
            reads = (frozenset(read_names(item.context_expr))
                     if item is not None else frozenset())
            if holder.borrowed:
                loop_machinery.append(FrameField(
                    name=fname, decl=f"{INDENT}{ftype}* {fname} = nullptr;\n",
                    borrows=reads))
                continue
            ctx_t = (self.types.get_resolved_type(item.context_expr)
                     if item is not None else None)
            drops = (ctx_t is None
                     or unwrap_ref_type(ctx_t).drop_runs_user_code())
            loop_machinery.append(FrameField(
                name=fname,
                decl=f"{INDENT}::tpy::frame_slot<{ftype}> {fname};\n",
                borrows=reads, drop_runs_user_code=drops))

        # In-flight exception slots for CFG-decomposed try-finally-with-
        # await bodies. std::exception_ptr default-constructs
        # to null; the catch arm sets it via std::current_exception().
        # bool pending-return flags need explicit init: NSDMI
        # = false. Value slots take the placeholder initializer the
        # prescan recorded with them.
        is_gen = self._is_generator_shape()
        for holder in state.try_finally_fields:
            fname, ftype, finit = holder.name, holder.cpp_type, holder.init
            # Generators always return StopIteration; no pending-return value
            # slot is needed. The field may be allocated by the prescan when
            # the trial build ran under the async shape -- skip it here.
            if is_gen and fname.startswith("__finally_ret_"):
                continue
            init = " = false" if ftype == "bool" else finit
            loop_machinery.append(FrameField(
                name=fname, decl=f"{INDENT}{ftype} {fname}{init};\n"))

        # A sub-future is reset where its await completes, so it holds
        # nothing when the body finishes.
        sub_futures = [
            FrameField(name=name, decl=decl, borrows=reads,
                       drop_runs_user_code=owns)
            for name, decl, reads, owns in self._resumable_sub_future_fields(
                func, cfg, record_name)]

        fields_ = (local_fields + alias_fields + loop_machinery
                   + frame_objects + sub_futures)
        printed.frame_fields = order_frame_fields(fields_)
        return printed.frame_fields

    def _loop_field_borrows(self, fname: str,
                            owner: FrameLoopOwner) -> 'frozenset[str]':
        """The source holds what its expression reads; every other loop field
        points into the source, held or named."""
        stmt, info = owner.stmt, owner.info
        reads = frozenset(read_names(stmt.iterable)) | frozenset(
            w.target for w in walrus_bindings(stmt))
        if info is None or fname == f"__for_src_{info.uid}":
            return reads
        src = f"__for_src_{info.uid}"
        if any(fn == src for fn, _ in info.fields):
            return frozenset([src])
        return reads

    def _with_field_items(self, func: TpyFunction) -> 'dict[str, TpyWithItem]':
        """Each `__with_ctx_<n>` field -> the `with` item whose manager it
        holds."""
        state = self._frame_of(func)
        out: dict[str, TpyWithItem] = {}

        def walk(stmts: 'list[TpyStmt]') -> None:
            for s in stmts:
                if isinstance(s, TpyWith):
                    decomposed = state.with_uid_map.get(s)
                    if decomposed is not None:
                        homes = [f"__with_ctx_{n}" for n in decomposed]
                    else:
                        homes = [h.name if h is not None else None
                                 for h in state.with_owned_ctx_map.get(s, ())]
                    for item, home in zip(s.items, homes):
                        if home is not None:
                            out[home] = item
                for b in s.sub_bodies():
                    walk(b)

        walk(self._frame_of(func).lifted_body)
        return out

    def _frame_local_borrows(
            self, func: TpyFunction,
    ) -> 'tuple[dict[str, set[str]], set[str]]':
        """The LIFETIME edges out of each local's field -- the fields (and
        other names) whose storage it may point into -- plus the locals whose
        borrows are not traced.

        * A frame object borrows the roots sema recorded for its bindings
          (`function_frame_local_roots`); a plain call's temporaries are the
          `__coro_arg_<n>` lifts of its statement.
        * A lift borrows what its expression reads.
        * A rebound local points into its rebind slots.
        * A borrow alias points into its source root.
        * A loop var (or unpack target) bound into its loop's storage points
          into the loop's fields and what the loop iterates.
        * A `with` target points into its manager."""
        state = self._frame_of(func)
        out: dict[str, set[str]] = {}
        untraced: set[str] = set()
        if not state.locals:
            return out, untraced

        def edge(src: str, dst: 'str | None') -> None:
            if dst is not None and dst != src:
                out.setdefault(src, set()).add(dst)

        for lift, (source, binds) in state.arg_lifts.items():
            for n in read_names(source):
                edge(lift, n)
            if binds is not None:
                edge(binds, lift)
        roots_of = self.ctx.analyzer.function_frame_local_roots.get(func, {})
        for loc in state.locals:
            lname = loc.name
            if not self._holds_frame_by_value(loc.type, loc.placement):
                continue
            fact = roots_of.get(lname)
            if fact is None:
                untraced.add(lname)
                continue
            roots, plain = fact
            if None in roots and not plain:
                untraced.add(lname)
            for root in roots:
                edge(lname, root)
        for slot in state.ptr_slot_fields:
            edge(slot.local, slot.name)
        for root, targets in state.const_source_edges.items():
            for target in targets:
                edge(target, root)
        by_loop: dict[int, list[str]] = {}
        for holder in state.for_fields:
            if holder.loop is not None:
                by_loop.setdefault(id(holder.loop.stmt), []).append(holder.name)
        for s, info in state.for_loop_info.items():
            bound = set(info.pointer_payloads) | set(
                info.pointer_form_unpack_targets)
            for n in (info.pointer_form_loop_var, info.borrow_tuple_loop_var,
                      info.opt_ptr_loop_var,
                      info.loop_var_field[0] if info.loop_var_field else None):
                if n is not None:
                    bound.add(n)
            for n in bound:
                for fname in by_loop.get(id(s), ()):
                    edge(n, fname)
                for r in read_names(s.iterable):
                    edge(n, r)
        # A loop the CFG keeps whole holds its source only because something
        # its body binds may point into an element.
        for s, held in state.for_src_fields.items():
            if held is None:
                continue
            for n in held.loop.bound_names:
                loc = state.local(n)
                if loc is not None and loc.placement.kind not in _OWNING_FRAME_KINDS:
                    edge(n, held.name)
        with_items = self._with_field_items(func)
        manager_of = {id(item): fname for fname, item in with_items.items()}

        def with_targets(stmts: 'list[TpyStmt]') -> None:
            for s in stmts:
                if isinstance(s, TpyWith):
                    for item in s.items:
                        if item.target is None:
                            continue
                        mgr = manager_of.get(id(item))
                        if mgr is not None:
                            edge(item.target, mgr)
                        for r in read_names(item.context_expr):
                            edge(item.target, r)
                for b in s.sub_bodies():
                    with_targets(b)

        with_targets(self._frame_of(func).lifted_body)
        return out, untraced

    @staticmethod
    def _holds_frame_by_value(ltype: TpyType,
                              verdict: 'FrameLocalLayout') -> bool:
        inner = unwrap_readonly(unwrap_own(unwrap_ref_type(ltype)))
        return (isinstance(inner, ConcreteFrameType)
                and verdict.kind not in (FrameLocalKind.PTR_ALIAS,
                                         FrameLocalKind.REBIND_PTR))

    def _emit_frame_local_field(self, out: "TextIO", lname: str, ltype,
                                verdict: 'FrameLocalLayout',
                                func: TpyFunction) -> None:
        """Render one hoisted local's field per its placement verdict. The
        classification itself lives in the frame builder's `_frame_layout` (shared with the
        body-context seeding and THIR admission)."""
        ltype_inner = unwrap_ref_type(ltype)
        cpp_name = escape_cpp_name(lname)
        kind = verdict.kind
        const_pfx = "const " if verdict.const else ""
        if kind is FrameLocalKind.SOURCE_FORM_SLOT:
            # The payload spelling came from the iteration source at
            # classification; frame_slot's specializations supply
            # either the alias or the owning form.
            out.write(f"{INDENT}::tpy::frame_slot<"
                      f"{verdict.payload}> {cpp_name};\n")
        elif kind is FrameLocalKind.OWNED_STR:
            out.write(f"{INDENT}std::string {cpp_name};\n")
        elif kind is FrameLocalKind.PTR_ALIAS:
            # A payload is spelled from the source and carries its
            # own const.
            inner_cpp = (verdict.payload
                         or const_pfx + self.types.type_to_cpp(ltype_inner))
            out.write(f"{INDENT}{inner_cpp}* {cpp_name} = nullptr;\n")
        elif kind is FrameLocalKind.OWNING_TUPLE_SLOT:
            storage_cpp = ltype_inner.to_cpp_stored()
            out.write(f"{INDENT}::tpy::frame_slot<{storage_cpp}> {cpp_name};\n")
        elif kind is FrameLocalKind.MIXED_TUPLE_SLOT:
            out.write(
                f"{INDENT}::tpy::frame_slot<{verdict.payload}> {cpp_name};\n")
        elif kind is FrameLocalKind.BORROW_TUPLE:
            bt = verdict.effective_type or ltype_inner
            cpp_type = self.types.tuple_borrow_cpp(bt, const=verdict.const)
            init = emit_prims.placeholder_init(bt, cpp_type)
            out.write(f"{INDENT}{cpp_type} {cpp_name}{init};\n")
        elif kind is FrameLocalKind.VALUE:
            cpp_type = self.types.type_to_cpp(ltype_inner)
            init = emit_prims.placeholder_init(ltype_inner, cpp_type)
            out.write(f"{INDENT}{cpp_type} {cpp_name}{init};\n")
        elif kind is FrameLocalKind.OPT_PTR:
            inner_cpp = self.types.type_to_cpp(
                unwrap_readonly(unwrap_readonly(ltype_inner).inner))
            out.write(
                f"{INDENT}{const_pfx}{inner_cpp}* {cpp_name} = nullptr;\n")
        elif kind is FrameLocalKind.REBIND_PTR:
            inner_cpp = self.types.type_to_cpp(
                unwrap_readonly(unwrap_own(ltype_inner)))
            out.write(f"{INDENT}{const_pfx}{inner_cpp}* {cpp_name} = nullptr;\n")
        else:
            cpp_type = self.types.type_to_cpp(ltype_inner)
            out.write(f"{INDENT}::tpy::frame_slot<{cpp_type}> {cpp_name};\n")

    # -- Struct definition ----------------------------------------------------

    def gen_coro_struct(self, out: "TextIO", func: TpyFunction,
                         record_name: str | None = None,
                         internal_linkage: bool = False) -> None:
        """Emit the full `__FCoro` struct definition. When `record_name`
        is given, the struct captures `__self: <Record>&` and the struct
        name is `__coro_<Record>_<func>` (mirrors generator-method
        codegen). `internal_linkage` says the caller places the struct in
        an anonymous namespace.
        """
        struct_name = self.gen_struct_name(func, record_name)
        ctor_params = self._classify_params(func, record_name)
        cfg = self.resumable_frame(func, record_name)
        yields = cfg.suspensions

        self.ctx.emit_declaration_echo(out, func.loc)
        self._emit_template_header(out, func, record_name=record_name)
        # Async coroutines are awaited, not iterated, so this is "" for them;
        # generator frames gain begin()/end() (see _generator_iter_base).
        base = self._generator_iter_base(func, record_name)
        out.write(f"struct {struct_name}{base} {{\n")

        # State (shared) + per-shape extra state (async adds the cancel
        # flag). ::tpy::frame_state deletes copy and assignment and panics
        # on a move once the frame was entered.
        dtor_cases = self._dtor_cleanup_cases(cfg)
        out.write(f"{INDENT}::tpy::frame_state __state;\n")
        self._emit_resumable_extra_state_fields(out)

        # Captured param fields
        for p in ctor_params:
            out.write(f"{INDENT}{p.field_decl()};\n")

        # Every other field -- hoisted locals, the `for` source aliases, loop,
        # rebind-slot, `with` and `try` machinery, frame objects, sub-futures
        # -- in one borrow-graph order (see `frame_fields`).
        for ff in self._frame_fields(func, record_name, cfg):
            out.write(ff.decl)
        is_gen = self._is_generator_shape()

        # Generators with helper-based finallies that contain a `return` need a
        # stop flag: the helper sets it so `__next__()` emits StopIteration
        # instead of re-throwing (Python: `return` in `finally` suppresses exc).
        if is_gen and cfg.finally_helpers_return:
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
        seeded = self._constructor_loop_seed(func)
        ctor_seed = seeded[1] if seeded is not None else None
        self._print_state(self._frame_of(func)).ctor_seeded_uid = (
            seeded[0] if seeded is not None else None)
        ctor_body = " ".join(ctor_seed or ())
        ctor_body = f"{{ {ctor_body} }}" if ctor_body else "{}"
        out.write(f"{INDENT}{struct_name}({ctor_param_list})"
                  f"{emit_prims.member_init_list(init_parts, INDENT)} {ctor_body}\n\n")
        if self._builds_source_in_place(func):
            # An rvalue source is built IN PLACE from a factory: the prvalue
            # the factory returns initializes the field directly, so a source
            # with no move constructor (a combinator pinned by the record
            # temporary it owns) still reaches the frame, and no source is
            # moved on the way in.
            src, rest = ctor_params[0], ctor_params[1:]
            make = self._in_place_factory_name(func)
            in_place_params = ", ".join(
                ["std::in_place_t", f"F_{make}&& {make}_"]
                + [p.ctor_param_decl() for p in rest])
            in_place_inits = [
                "__state(S_INITIAL)", *self._resumable_extra_ctor_inits(),
                f"{src.cpp_name}(std::forward<F_{make}>({make}_)())",
                *(p.ctor_init() for p in rest)]
            out.write(f"{INDENT}template <typename F_{make}>\n")
            out.write(f"{INDENT}{struct_name}({in_place_params})"
                      f"{emit_prims.member_init_list(in_place_inits, INDENT)} {{}}\n\n")

        if dtor_cases:
            # Keyed on the whole body's fact, a superset of the cleanup
            # actions the destructor runs (no per-action fact exists).
            self._emit_frame_dtor(
                out, struct_name, dtor_cases,
                emit_prims.defer_signals_cpp(frame_function_info(self.ctx.analyzer, func, record_name)))

        # Body-method forward declaration + per-shape extra methods.
        out.write(f"{INDENT}{self._resumable_body_method_fwd_decl(func)};\n")
        self._emit_resumable_extra_methods(out, struct_name)

        # Finally-helper forward declarations: one per TryRegion with a
        # finally body.
        for helper_name, _body in cfg.finally_helpers:
            out.write(f"{INDENT}void {helper_name}();\n")

        # Nested defs become frame member functions (callable from every
        # resume case; frame-field access via implicit this).
        for nd in (slot.source for slot in cfg.nested_defs):
            params_str, nd_ret = emit_prims.nested_def_signature(
                self.types, nd.func)
            ret_str = nd_ret if nd_ret is not None else "void"
            out.write(f"{INDENT}{ret_str} "
                      f"{escape_cpp_name(nd.func.name)}({params_str});\n")

        repr_label = (f"{record_name}.{func.name}" if record_name
                      else func.name)
        repr_kind = "generator" if self._is_generator_shape() else "coroutine"
        param_struct_name = self._struct_name_templated(func, record_name)
        # An in-class friend of an anonymous-namespace struct has internal
        # linkage, and clang's -Wunused-function (-Werror) fires when no
        # body prints the frame; gcc stays quiet.
        unused_ok = "[[maybe_unused]] " if internal_linkage else ""
        out.write(f"\n{INDENT}{unused_ok}friend std::ostream& operator<<("
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
        self.ctx.emit_declaration_echo(out, func.loc)
        self._emit_template_header(out, func)
        params = self._emit_params_decl(func)
        out.write(f"{struct_name} {escape_cpp_name(func.name)}({params}) {{\n")
        args = self._factory_args_forwarded(func)
        out.write(f"{INDENT}return {struct_name}({args});\n")
        out.write(f"}}\n")
        if self._builds_source_in_place(func):
            header, ret, in_place_params, in_place_args = self._in_place_factory_parts(func)
            out.write(f"{header}\n{ret} {escape_cpp_name(func.name)}({in_place_params}) {{\n")
            out.write(f"{INDENT}return {ret}({in_place_args});\n")
            out.write(f"}}\n")

    def _builds_source_in_place(self, func: TpyFunction) -> bool:
        """A genexpr frame whose source slot is a deduced type can be handed an
        rvalue source, which it builds in place. A borrowed container keeps its
        concrete type and is only ever bound by reference."""
        if not func.is_genexpr:
            return False
        cparams = self._classify_params(func, None)
        return bool(cparams) and cparams[0].kind is FrameCaptureKind.STATIC_PROTOCOL

    def _in_place_factory_parts(self, func: TpyFunction) -> tuple[str, str, str, str]:
        """The genexpr factory overload that builds the source in place:
        its template header, the frame type it returns (the source slot
        deduced from what the factory argument returns), its params and the
        args it forwards to the frame's in-place constructor."""
        cparams = self._classify_params(func, None)
        rest = cparams[1:]
        make = self._in_place_factory_name(func)
        parts = [f"typename {tp}" for tp in func.type_params]
        parts.append(f"typename F_{make}")
        parts.extend(f"typename {p.field_type}" for p in rest)
        targs = (list(func.type_params) + [f"std::invoke_result_t<F_{make}>"]
                 + [p.field_type for p in rest])
        ret = f"{self.gen_struct_name(func, None)}<{', '.join(targs)}>"
        params = ", ".join(["std::in_place_t", f"F_{make}&& {make}"]
                           + [p.factory_param_decl() for p in rest])
        args = ", ".join(
            ["std::in_place", f"std::forward<F_{make}>({make})"]
            + [f"std::forward<{p.ctor_param_type}>({p.cpp_name})" for p in rest])
        return f"template <{', '.join(parts)}>", ret, params, args

    @staticmethod
    def _in_place_factory_name(func: TpyFunction) -> str:
        """The name of the source factory's param in the in-place overloads
        (`F_<name>` its deduced type), distinct from every capture -- a capture
        spells `<name>` and `F_<name>` the same way."""
        name = "make"
        while name in func.capture_params:
            name += "_"
        return name

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
            if cparam.kind in _DEDUCED_SLOT_KINDS:
                parts.append(
                    f"std::forward<{cparam.ctor_param_type}>({cparam.cpp_name})")
            elif cparam.kind in _MOVED_IN_KINDS:
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
        aliased_vars, etc. populated from sema scan).
        Layers the resumable-frame fields (`generator_field_names`,
        `generator_self_ref`, etc.) plus `in_generator_body=True` on top.
        """
        old_in_gen = self.ctx.in_generator_body
        old_field_names = self.ctx.generator_field_names
        old_forwarded_locals = self.ctx.generator_forwarded_locals
        old_optional_fields = self.ctx.generator_optional_fields
        old_frame_slot_locals = self.ctx.generator_frame_slot_locals
        old_borrow_form_loop_vars = self.ctx.generator_borrow_form_loop_vars
        old_frame_const_bindings = self.ctx.frame_const_bindings
        old_self_ref = self.ctx.generator_self_ref
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

        # Reset frame-specific fields before setup_body_scope, since the
        # `setup_resumable_frame_locals` call inside it writes to
        # `generator_optional_fields`.
        self.ctx.generator_field_names = set()
        self.ctx.generator_optional_fields = set()
        self.ctx.generator_frame_slot_locals = set()
        self.ctx.generator_borrow_form_loop_vars = set()
        self.ctx.frame_const_bindings = set(
            self._frame_of(func).const_bindings)

        local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
        for pname, ptype in func.params:
            local_ns.bind_variable(pname, ptype)

        # NOT the frame's capture verdict (`frame_capture_const_names`), and
        # the two are not interchangeable: this pair is what the BODY's
        # signature model assumes per param, so it names value captures the
        # frame holds by copy (a scalar, an owned `str`) which never produce a
        # const field, while the capture verdict adds the explicit
        # `readonly[T]` / ptr-variant deep half the body model does not reach.
        # Measured over the corpus: they diverge in BOTH directions (TODO.md).
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
        # Nested defs are frame members: register their names up front so
        # call sites in any resume case (and finally-helper bodies) render
        # the unqualified member call.
        for nd in (slot.source for slot in self._frame_of(func).nested_defs):
            self.ctx.nested_def_locals.add(nd.func.name)
            self.ctx.local_scope_names.add(nd.func.name)
        if record_name:
            self.ctx.generator_self_ref = "__self"
            self.ctx.generator_field_names.add("__self")
        else:
            self.ctx.generator_self_ref = None
        for pname, _ in func.params:
            self.ctx.generator_field_names.add(pname)
        for loc in self._frame_of(func).locals:
            self.ctx.generator_field_names.add(loc.name)
        # Forwarded proto-param aliases have no field of their own, but they
        # are still frame-resident names (storage resolves to the backing
        # param) -- register them so the field-name membership tests fire and
        # route through `generator_storage_name`.
        self.ctx.generator_forwarded_locals = dict(func.forwarded_locals or {})
        for lname in self.ctx.generator_forwarded_locals:
            self.ctx.generator_field_names.add(lname)

        try:
            with self.ctx.temps.function_scope():
                yield
        finally:
            self.ctx.in_generator_body = old_in_gen
            self.ctx.generator_field_names = old_field_names
            self.ctx.generator_forwarded_locals = old_forwarded_locals
            self.ctx.generator_optional_fields = old_optional_fields
            self.ctx.generator_frame_slot_locals = old_frame_slot_locals
            self.ctx.generator_borrow_form_loop_vars = old_borrow_form_loop_vars
            self.ctx.frame_const_bindings = old_frame_const_bindings
            self.ctx.generator_self_ref = old_self_ref
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
        cfg = self.resumable_frame(func, record_name)
        if not cfg.finally_helpers and not cfg.nested_defs:
            return
        struct_name = self._struct_name_templated(func, record_name)
        # Helper-finally statements come from the same lowering attempt as the
        # body (the second lookup is cached), and render through the installed
        # leaf emitter like any other BB leaf.
        leaf = self._thir_resumable_leaf_emitter(func, record_name, cfg)

        with self._resumable_frame_ctx(func, record_name):
            with self._thir_leaf_scope(leaf):
                for helper_name, helper_slots in cfg.finally_helpers:
                    self._emit_template_header(
                        out, func, record_name=record_name)
                    out.write(f"void {struct_name}::{helper_name}() {{\n")
                    self.ctx.indent_level = 1
                    with self._generator_finally_helper_scope():
                        for slot in helper_slots:
                            leaf.emit_leaf_stmt(
                                out, slot, self.ctx.indent_level)
                    self.ctx.indent_level = 0
                    out.write(f"}}\n")
            for nd_slot in cfg.nested_defs:
                nd = nd_slot.source
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
                leaf.emit_nested_def_body(out, nd_slot, self.ctx.indent_level)
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
        rep = classify_result_representation(
            yt, position=ResultPosition.GENERATOR_YIELD,
            generic_borrows=func.generic_yield_borrows)
        if rep is ResultRepresentation.YIELD_TUPLE:
            return yt.to_cpp_return()
        cpp = self.types.type_to_cpp(yt)
        if rep in (ResultRepresentation.YIELD_BORROW,
                   ResultRepresentation.YIELD_TRAIT):
            return yield_borrow_slot_cpp(yt, cpp)
        return cpp

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
        the GENERATOR_YIELD result classification -- borrow form
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
        """Per-shape state fields beyond the shared `::tpy::frame_state __state`.
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
            cfg: 'THIRResumableFrame') -> 'dict[int, list[str]]':
        """The `T_<pname>` template args each suspension's sub-coro struct
        name needs, keyed by suspension index.

        Rendered here rather than where the callee is resolved, because the
        spelling must equal what the emplace hands the ctor and the emplace
        coercions are only decidable under the frame body scope: the `self`
        -> `__self` rewrite and the frame-slot deref come from that scope,
        and so do the narrowing facts the coercions read. Entering it is also
        what keeps `frame_field_shadows` this body's, rather than whatever the
        previously emitted body left behind.

        Nothing is published from here: the render is discarded, so it drives
        no emitted move. The body walk renders the authoritative spelling when
        it emits the same argument at the emplace.

        The leaf emitter is installed for the same reason the body scope is:
        a routed body renders its emplace arguments off its own lowered
        nodes, so the capture type has to be read from the same author or the
        two spellings can only agree by accident.
        """
        pending = [y for y in cfg.suspensions
                   if y.payload.prebuilt_slot is None
                   and self._protocol_param_positions(y.payload.operand_expr)]
        if not pending:
            return {}
        leaf = self._thir_resumable_leaf_emitter(func, record_name, cfg)
        args: dict[int, list[str]] = {}
        with self._resumable_frame_ctx(func, record_name):
            with self._thir_leaf_scope(leaf):
                for y in pending:
                    extra = self._extra_template_args_for_await(y.payload)
                    if extra:
                        args[y.suspension_index] = extra
        return args

    def _resumable_sub_future_fields(
            self, func: TpyFunction, cfg: 'THIRResumableFrame',
            record_name: str | None = None
    ) -> 'list[tuple[str, str, frozenset[str], bool]]':
        """Sub-future frame fields -- async-only (policy seam), as (name,
        declaration, the names its operand reads, whether it owns a frame).
        One per Yield (suspension_index = field ordinal): Inline ->
        optional<sub-coro struct>, Erased -> optional<value awaitable>,
        Borrowed -> raw pointer. Async-with's synthetic yields override
        sub_field_cpp_type via the state.async_with_struct_names map (the CM's
        __aenter__/__aexit__ coro struct, computed at prescan time). The
        generator shape stores nothing at a `yield`, so it has none.
        """
        if self._is_generator_shape():
            return []
        fields: 'list[tuple[str, str, frozenset[str], bool]]' = []
        state = self._frame_of(func)
        struct_names = state.async_with_struct_names
        for_struct_names = state.async_for_struct_names
        protocol_args = self._protocol_template_args_by_suspension(
            func, record_name, cfg)
        for y in cfg.suspensions:
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
                    sub_cpp = entry[0] if p.async_with_kind is AsyncWithKind.AENTER else entry[1]
            elif p.async_for_uid is not None:
                entry = for_struct_names.get(p.async_for_uid)
                if entry is not None:
                    sub_cpp = entry
            name = f"__sub_{y.suspension_index}"
            reads = (frozenset(read_names(p.operand_expr))
                     if p.operand_expr is not None else frozenset())
            if p.mode is AwaitMode.BORROWED:
                fields.append((name, f"{INDENT}{sub_cpp}* {name} = nullptr;\n",
                               reads, False))
            else:
                fields.append((name,
                               f"{INDENT}std::optional<{sub_cpp}> {name};\n",
                               reads, True))
        return fields

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
        the frame is its own iterator)."""
        if self._is_generator_shape():
            out.write(f"{INDENT}{struct_name}& __iter__() "
                      f"{{ return *this; }}\n")
            return
        out.write(f"{INDENT}void cancel() {{ __cancel_pending = true; }}\n")

    def gen_coro_poll_def(self, out: "TextIO", func: TpyFunction,
                            record_name: str | None = None, *,
                            linkage: 'Callable[[int], str] | None' = None) -> None:
        """Emit the frame's body method, wherever the caller places it: the
        .cpp for an async `__poll__`, inline-in-hpp for a template, and
        `<mod>_inl.hpp` or the .cpp by size for a non-template generator's
        `__next__` (`CodeGenerator._emit_generator_next`). When
        `record_name` is given, the struct is `__coro_<Record>_<func>` and
        the body sees `self.X` as `__self.X` (parallels generator methods).

        `linkage` is asked for the definition's specifier once its size is
        known -- the number of code lines it renders to, source echo excluded
        -- so a caller that places the definition by size never has to take
        the rendered text apart to add one."""
        struct_name = self._struct_name_templated(func, record_name)
        cfg = self.resumable_frame(func, record_name)
        has_yields = bool(cfg.suspensions)
        leaf = self._thir_resumable_leaf_emitter(func, record_name, cfg)

        self.ctx.emit_definition_echo(out, func.loc, markers=self._resume_markers(cfg))
        self._emit_template_header(out, func, record_name=record_name)
        body = _CodeLineCounter()
        self._emit_resumable_body_prelude(body, has_yields)

        # Return-rewrite setup (the C++ Poll<T> type and DONE state
        # label so `return v` lowers correctly inside the state machine)
        # is the resumable-shape policy's responsibility.
        has_finally_stop = (
            self._is_generator_shape()
            and cfg.finally_helpers_return)
        with self._generator_finally_stop_scope(has_finally_stop):
            with self._resumable_frame_ctx(func, record_name):
                with self._resumable_return_lowering(func):
                    with self._thir_leaf_scope(leaf):
                        self._emit_state_machine(body, func, cfg)

        # The declarator line and the closing brace count too.
        specifier = linkage(body.code_lines + 2) if linkage is not None else ""
        out.write(f"{specifier}"
                  f"{self._resumable_body_method_decl(func, struct_name)} {{\n")
        out.write(body.getvalue())
        out.write(f"}}\n")

    # -- THIR resumable seam ----------------------------------------------
    # The state-machine skeleton (this module + the published frame) is SHARED
    # machinery, like signatures and struct layout; only the user-source
    # LEAVES route through THIR. A routed body swaps every leaf-delegation
    # site (BB leaf stmts, RaiseT stmts, Branch conds, emplace args, the
    # async-return value in _make_async_return) to the leaf emitter below;
    # per-body routing stays all-or-nothing.

    def _thir_resumable_attempt(self, func: TpyFunction,
                                record_name: str | None,
                                cfg: 'THIRResumableFrame'):
        """Attempt-once THIR leaf lowering for this frame body, cached so a
        re-entered frame lowers exactly once."""
        cache = self.ctx.thir_resumables
        if func in cache:
            return cache[func]
        from ..thir.reject import (begin_attempt, commit_attempt,
                                   reject_attempt)
        from ..thir.lower.resumable import lower_resumable
        begin_attempt()
        rb = lower_resumable(cfg, self.ctx.analyzer, self.types.type_to_cpp,
                             render_type_stored=self.types.type_to_cpp_stored,
                             native_globals=self.ctx.native_global_names)
        cache[func] = rb
        if rb is None:
            reject_attempt("resumable", func)
        commit_attempt()
        return rb

    def _thir_resumable_leaf_emitter(self, func: TpyFunction,
                                     record_name: str | None,
                                     cfg: 'THIRResumableFrame'):
        """The body's leaf renderer bound to the live ctx sinks."""
        rb = self._thir_resumable_attempt(func, record_name, cfg)
        from ..thir.emit import (ModuleCounter, CtxIterCounter,
                                 TempSink, ResumableLeafEmitter)

        def _return_hook(ret: 'THIRResumableReturn',
                         indent_level: int) -> str:
            # Nested leaf return: the same scaffolding a return terminator
            # gets, rendered from the node's own value and recipe.
            return self._resumable_return_code(ret, INDENT * indent_level)

        return ResumableLeafEmitter(
            rb,
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

    def _resumable_return_code(self, ret: 'THIRResumableReturn',
                               indent: str) -> str:
        """Lower a source-level `return` (its lowered `THIRResumableReturn`)
        for the frame shape currently being emitted -- the flags
        `_resumable_return_lowering` and the generator finally-helper scope
        set around the whole body emission."""
        if self.ctx.in_async_coro_body:
            return self._make_async_return(ret, indent)
        if self.ctx.in_generator_resumable_body:
            # Generator on the resumable frame: bare return / end ->
            # StopIteration done, via the while/switch (no __done label).
            return self._make_generator_resumable_return(ret, indent)
        if self.ctx.in_generator_finally_helper:
            # return inside a helper-based finally body: set the stop flag
            # and void-return; __next__() checks __finally_stop after the
            # helper call and emits StopIteration (Python: return in finally
            # suppresses any pending exception).
            return (f"{indent}this->__finally_stop = true;\n"
                    f"{indent}return;\n")
        raise CodeGenError(
            "internal: resumable return outside a resumable body emission")

    def _async_return_value_cpp(self, ret: 'THIRResumableReturn', ret_type,
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
        # return-shape gate must revisit this seam.
        return self._leaf.render_return_value(ret, allow_move=allow_move)

    def _make_async_return(self, ret: 'THIRResumableReturn',
                           indent: str) -> str:
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
            if pending_slot is not None and ret.has_operand:
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
                expr_cpp = self._async_return_value_cpp(ret, ret_type,
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
        if (not isinstance(ret_type, VoidType) and ret.has_operand
                and self.ctx.finally_stack):
            recipe = None
            if ret.deferred is not None:
                # A stamped return ALWAYS has a recipe: lowering rejects the
                # whole body when it cannot build one.
                recipe = self._leaf.render_deferred_return(ret)
            if recipe is not None:
                captures, deferred_materialize = recipe
                chain = io.StringIO()
                terminated = emit_prims.emit_finally_chain(self.ctx, chain,
                                                           indent)
                maybe_unused = "[[maybe_unused]] " if terminated else ""
                for line in captures:
                    out.write(f"{indent}{maybe_unused}{line}\n")
                out.write(chain.getvalue())
            else:
                expr_cpp = self._async_return_value_cpp(ret, ret_type,
                                                        to_borrow=True)
                ret_tmp = f"__tpy_async_ret_{self.ctx.iter_counter}"
                self.ctx.iter_counter += 1
                chain = io.StringIO()
                terminated = emit_prims.emit_finally_chain(self.ctx, chain,
                                                           indent)
                maybe_unused = "[[maybe_unused]] " if terminated else ""
                # For deferral-INELIGIBLE reference shapes (declared unions,
                # ...) this eager capture is a KNOWN-WRONG pre-chain
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
            if not ret.has_operand:
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
                    expr_cpp = self._async_return_value_cpp(ret, ret_type,
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

    def _make_generator_resumable_return(self, ret: 'THIRResumableReturn',
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
        return self._generator_stop_return(indent)

    def _generator_stop_return(self, indent: str) -> str:
        """The generator `return` from the current finally-frame stack and
        pending-return context (see `_make_generator_resumable_return`)."""
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

    def resumable_frame(self, func: TpyFunction,
                        record_name: str | None = None) -> THIRResumableFrame:
        """The frame THIR publishes for `func`, built at its first request.

        `record_name` (the enclosing record, for a method) selects the
        owning record's method when the per-param const verdict is looked
        up, so a generator method must pass it too: without it the lookup
        answers None, or worse resolves off a same-named free function.
        The build reads the current resumable shape (the emitter's default
        ASYNC one at the generator gate, which is a known quirk)."""
        frames = self.ctx.thir_resumable_frames
        frame = frames.get(func)
        if frame is None:
            frame = build_resumable_frame(func, record_name, self._shape,
                                          self._frame_deps())
            frames[func] = frame
        return frame

    def _frame_of(self, func: TpyFunction) -> THIRResumableFrame:
        """The already-published frame of `func`."""
        return self.ctx.thir_resumable_frames[func]

    def _frame_deps(self) -> FrameBuildDeps:
        return FrameBuildDeps(
            analyzer=self.ctx.analyzer,
            type_to_cpp=self.types.type_to_cpp,
            tuple_borrow_cpp=self.types.tuple_borrow_cpp,
            resolve_type=self.types.resolve_type,
            resolved_type_in=self.types.resolved_type_in,
            is_static_protocol_param=(
                self.functions.protocols.is_static_protocol_param),
            same_module_generators=self.same_module_generators,
            cycle_peers=self.ctx.cycle_peers, ret_cpp=self._ret_cpp)

    def _print_state(self, frame: THIRResumableFrame) -> '_FramePrint':
        """The printer's own per-frame state (emit-order caches, the
        constructor's loop seed), keyed by the frame object."""
        state = self.ctx.resumable_print.get(frame)
        if state is None:
            state = self.ctx.resumable_print[frame] = _FramePrint()
        return state

    def _resume_to_yield(self, frame: THIRResumableFrame) -> 'dict[int, THIRSuspend]':
        """`resume block -> suspension` map, built once per frame."""
        state = self._print_state(frame)
        if state.resume_to_yield is None:
            state.resume_to_yield = {y.resume_bb: y for y in frame.suspensions}
        return state.resume_to_yield

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
            owner, method, inferred_type_args,
            analyzer=self.ctx.analyzer, type_to_cpp=self.types.type_to_cpp,
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

    def _extra_template_args_for_await(
            self, payload: 'THIRAwaitSuspension') -> list[str]:
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
        build, not a style difference. An argument temporary renders there
        as `std::declval` of its type (`UnevaluatedTemps`): decltype doesn't
        evaluate, and the same args re-render at emplace time.
        """
        call = payload.operand_expr
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
        # The class-scope `decltype` has no statement to declare an
        # argument temporary in: it renders as `std::declval` of its type.
        from ..thir.emit import UnevaluatedTemps
        rendered = self._leaf.render_await_args(
            payload.args, temps=UnevaluatedTemps(self.ctx))
        # `await_arg_capture_t` models the callee factory's `T&&` deduction:
        # lvalue arg -> `U&` (borrow), rvalue -> `U` (own). `decltype((arg))`
        # (double parens) carries the value category.
        return [f"::tpy::await_arg_capture_t<decltype(({rendered[i]}))>"
                for i in positions]

    def _compute_case_entries(self, cfg: 'THIRResumableFrame') -> dict[int, _StateLabel]:
        """Return mapping bb_id -> StateLabel for every block the frame
        publishes as a case entry (`THIRResumableFrame.case_entries`, in
        discovery order): S_INITIAL for the entry block, S_RESUME_<i> for a
        suspension's resume block, S_JOIN_<n> numbered in order for the
        rest. Cached per frame so the struct-emit and poll-emit passes
        share the labels."""
        state = self._print_state(cfg)
        if state.case_entries is not None:
            return state.case_entries
        resume = self._resume_to_yield(cfg)
        case_entries: dict[int, _StateLabel] = {}
        join_idx = 0
        for bid in cfg.case_entries:
            if bid == cfg.entry_block:
                case_entries[bid] = _StateLabel(_StateKind.INITIAL)
            elif bid in resume:
                case_entries[bid] = _StateLabel(
                    _StateKind.RESUME, resume[bid].suspension_index)
            else:
                case_entries[bid] = _StateLabel(_StateKind.JOIN, join_idx)
                join_idx += 1
        state.case_entries = case_entries
        return case_entries

    def _resume_markers(self, cfg: 'THIRResumableFrame') -> dict[int, list[str]]:
        """Source line -> the resume states that begin after it, for the
        definition echo above the frame body."""
        markers: dict[int, list[str]] = {}
        for y in cfg.suspensions:
            loc = y.payload.source_loc
            if loc is not None:
                markers.setdefault(loc.line, []).append(f"S_RESUME_{y.suspension_index}")
        return markers

    def _resume_sources(self, cfg: 'THIRResumableFrame') -> dict[int, str]:
        """Suspension index -> the source line it resumes after, for the
        case-label notes in the state machine."""
        sources: dict[int, str] = {}
        for y in cfg.suspensions:
            text = self.ctx.source_line_text(y.payload.source_loc)
            if text is not None:
                sources[y.suspension_index] = text
        return sources

    def _emit_state_machine(self, out: "TextIO", func: TpyFunction,
                             cfg: 'THIRResumableFrame') -> None:
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
        # Case-label order matches the enum in gen_coro_struct. Every body is
        # rendered once, in that order, before any is written: a body that
        # only re-dispatches shares its target's case instead, so a loop pays
        # one dispatch per element rather than one per empty join/resume hop.
        order = sorted(case_entries.items(), key=lambda kv: kv[1])
        plan = self._single_loop_plan(func, cfg, case_entries, has_dtor)
        if plan is not None:
            self._emit_single_loop(out, func, cfg, case_entries, plan)
            self.ctx.indent_level = 0
            return
        after = self._resume_sources(cfg)
        bodies: dict[int, str] = {}
        for bb_id, _label in order:
            buf = io.StringIO()
            self.ctx.indent_level = 2
            self._emit_case(buf, cfg, bb_id, case_entries, func)
            self.ctx.indent_level = 1
            bodies[bb_id] = buf.getvalue()
        jumps = {bb_id: self._redispatch_target(cfg, bb_id, case_entries, func)
                 for bb_id in bodies}
        stacked: dict[int, list[_StateLabel]] = {}
        forwarded: set[int] = set()
        for bb_id, label in order:
            seen = {bb_id}
            cur = jumps[bb_id]
            while cur is not None and cur not in seen and jumps[cur] is not None:
                seen.add(cur)
                cur = jumps[cur]
            if cur is not None and cur not in seen:
                forwarded.add(bb_id)
                stacked.setdefault(cur, []).append(label)

        def note_for(label: _StateLabel) -> str:
            if not self.ctx.options.emit_source_comments:
                return ""
            if label.kind is _StateKind.INITIAL:
                return "  // entry"
            if label.kind is _StateKind.RESUME and label.idx in after:
                return f"  // after: {after[label.idx]}"
            return ""
        if cfg.suspensions:
            out.write(f"{inner}while (true) switch (__state) {{\n")
        else:
            out.write(f"{inner}switch (__state) {{\n")
        # The entry case marks the frame done before any body code runs: a
        # yield overwrites it with its resume point, a return or the end of
        # the body stores it anyway, and an exception escaping the first
        # step finishes the frame, as CPython's. It also makes the frame read
        # "entered" to ::tpy::frame_state's move guard.
        entered = (f"{inner}{INDENT}__state = S_DONE;  "
                   f"// until a yield sets where to resume\n")
        for bb_id, label in order:
            if bb_id in forwarded:
                continue
            # INITIAL is 0, so it leads its stack and the store it falls
            # through from runs on entry only.
            for alias in stacked.get(bb_id, ()):
                out.write(f"{inner}case {alias.cpp_name()}:{note_for(alias)}\n")
                if alias.kind is _StateKind.INITIAL:
                    out.write(entered)
                    out.write(f"{inner}{INDENT}[[fallthrough]];\n")
            out.write(f"{inner}case {label.cpp_name()}: {{{note_for(label)}\n")
            if label.kind is _StateKind.INITIAL:
                out.write(entered)
            out.write(bodies[bb_id])
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

    def _single_loop_plan(self, func: TpyFunction, cfg: 'THIRResumableFrame',
                          case_entries: dict[int, _StateLabel],
                          has_dtor: bool) -> '_LoopForm | None':
        """The loop form of a generator whose ONLY resume point is the head of
        its one loop, or None when the frame is not that shape.

        Such a frame has two live cases: the entry, which only seeds the loop,
        and the loop head, which every yield resumes at and every `continue`
        re-enters. Dispatching on `__state` to reach the one place control can
        be buys nothing, and behind an out-of-line consumer the dispatch plus
        a state store per element is the whole gap to a hand-written loop.
        `__state` keeps only what still needs remembering: that the seed ran
        (unless the constructor ran it), and that the source is exhausted
        (unless asking it again is free and harmless -- a begin/end pair or a
        range counter).

        Read off the CFG, before anything is rendered: a case that would only
        re-dispatch is a chain of empty `Fall` blocks."""
        if not (self._is_generator_shape() and func.is_genexpr):
            return None
        if has_dtor or cfg.finally_helpers:
            return None
        plain = (THIRFall, THIRBranch, THIRSuspend, THIRUnreachable,
                 THIRForAdvance)
        if any(bb.region_stack or not isinstance(bb.terminator, plain)
               for bb in cfg.blocks.values()):
            return None
        heads = [bb_id for bb_id, bb in cfg.blocks.items()
                 if isinstance(bb.terminator, THIRForAdvance)]
        if len(heads) != 1 or heads[0] not in case_entries:
            return None
        loop_bb = heads[0]

        def forwards_to_head(bb_id: int) -> bool:
            seen: set[int] = set()
            while bb_id != loop_bb:
                if bb_id in seen:
                    return False
                seen.add(bb_id)
                nxt = self._redispatch_target(cfg, bb_id, case_entries, func)
                if nxt is None:
                    return False
                bb_id = nxt
            return True

        init = [bb_id for bb_id, label in case_entries.items()
                if label.kind is _StateKind.INITIAL]
        if len(init) != 1 or init[0] == loop_bb:
            return None
        # The entry runs its statements (the seed) and falls into the head.
        last = cfg.blocks[self._fall_chain(cfg, init[0], case_entries)[-1]]
        if not (isinstance(last.terminator, THIRFall)
                and forwards_to_head(last.terminator.next_bb)):
            return None
        others = [bb_id for bb_id in case_entries
                  if bb_id != loop_bb and bb_id != init[0]]
        if not all(forwards_to_head(bb_id) for bb_id in others):
            return None
        advance = cfg.blocks[loop_bb].terminator
        info = self._for_info(func, advance.uid)
        idempotent = info is not None and info.strategy in ("begin_end", "range")
        head = cfg.blocks[loop_bb]
        exhausted = cfg.blocks[advance.exhausted_bb]
        # The test can lead the loop only where it leads the head's case and
        # running out is nothing but the terminal return.
        may_hoist = (idempotent and not head.stmts and not head.entry_narrowings
                     and advance.exhausted_bb not in case_entries
                     and not exhausted.stmts
                     and isinstance(exhausted.terminator, THIRUnreachable))
        loop_label = case_entries[loop_bb].cpp_name()
        return _LoopForm(
            head_labels=frozenset(
                [loop_label] + [case_entries[b].cpp_name() for b in others]),
            loop_label=loop_label, init_bb=init[0], loop_bb=loop_bb,
            records_done=not idempotent,
            may_hoist_test=may_hoist)

    @staticmethod
    def _fall_chain(cfg: 'THIRResumableFrame', start_bb: int,
                    case_entries: dict[int, _StateLabel]) -> list[int]:
        """The blocks one case walks inline from `start_bb` along `Fall`
        edges, up to the next case entry."""
        chain = [start_bb]
        while True:
            t = cfg.blocks[chain[-1]].terminator
            if not isinstance(t, THIRFall) or t.next_bb in case_entries:
                return chain
            chain.append(t.next_bb)

    def _emit_single_loop(self, out: "TextIO", func: TpyFunction,
                          cfg: 'THIRResumableFrame',
                          case_entries: dict[int, _StateLabel],
                          form: _LoopForm) -> None:
        inner = INDENT
        stop = "::tpy::make_unexpected(::tpy::StopIteration{})"
        self._loop_form = form
        try:
            self.ctx.indent_level = 2
            seed = io.StringIO()
            self._emit_case_body(seed, cfg, form.init_bb, case_entries, func,
                                 tail=True)
            body = io.StringIO()
            self._emit_case_body(body, cfg, form.loop_bb, case_entries, func,
                                 tail=True)
            self.ctx.indent_level = 1
        finally:
            self._loop_form = None
        if form.records_done:
            out.write(f"{inner}if (__state == S_DONE) return {stop};\n")
        if seed.getvalue():
            # Past the seed the state is the loop head, so this stays the one
            # compare per pull. Anything but INITIAL here means the seed threw
            # on an earlier pull (it runs marked done): the frame is finished.
            out.write(f"{inner}if (__state != {form.loop_label}) {{\n")
            out.write(f"{inner}{INDENT}if (__state != S_INITIAL) "
                      f"return {stop};\n")
            out.write(f"{inner}{INDENT}__state = S_DONE;  "
                      f"// until the seed completes\n")
            out.write(seed.getvalue())
            out.write(f"{inner}{INDENT}__state = {form.loop_label};\n")
            out.write(f"{inner}}}\n")
        # Where nothing has to be recorded on the way out the exhaustion test
        # is the loop's own condition, with the terminal return AFTER the
        # loop: gcc lays the in-loop spelling out with the cold return on the
        # hot path (a measured 12% on an out-of-line consumer).
        if form.condition is not None:
            out.write(f"{inner}while ({form.condition}) {{\n")
            out.write(body.getvalue())
            out.write(f"{inner}}}\n")
            if form.records_done:
                out.write(f"{inner}__state = S_DONE;\n")
            out.write(f"{inner}return {stop};\n")
        else:
            out.write(f"{inner}for (;;) {{\n")
            out.write(body.getvalue())
            out.write(f"{inner}}}\n")
            out.write(f"{inner}__builtin_unreachable();\n")

    def _transfer(self, indent: str, label: str, *, tail: bool) -> str:
        """A transfer of control to the case `label`. In the loop form the
        only such case is the loop head: `continue`, or nothing where the
        loop body ends anyway -- gcc compiles a redundant `else { continue; }`
        arm of a filter into a measurably slower loop (10% behind an
        out-of-line consumer)."""
        form = self._loop_form
        if form is None:
            return _state_jump(indent, label)
        assert label in form.head_labels, (label, form.head_labels)
        return "" if tail else f"{indent}continue;\n"

    def _constructor_loop_seed(self, func: TpyFunction
                               ) -> 'tuple[int, list[str]] | None':
        """The loop a genexpr frame seeds in its CONSTRUCTOR and the seeding
        statements, or None when it seeds at the first pull.

        CPython takes `iter(source)` where the genexpr is written, and a
        begin/end pair over a BORROWED container points outside the frame, so
        seeding early does not pin the frame. (A container grown while the
        genexpr is live invalidates the pair. The creation's iteration loan
        reports a loop body that grows it, and the frame loop's loans on the
        captures naming the source report the frame's own body.) A source
        the frame owns is seeded at the first pull: its iterators would point
        into the frame, which may still move before then."""
        loop = func.genexpr_loop
        if loop is None or not isinstance(loop.iterable, TpyName):
            return None
        # Only the source loop: an inner `for` clause's source is evaluated
        # in the body, per outer element, so it is never seeded here.
        state = self._frame_of(func)
        uid = state.for_uid_map.get(loop)
        info = state.for_info_by_uid.get(uid) if uid is not None else None
        if info is None:
            return None
        if info.strategy != "begin_end" or self._for_has_src_slot(info, uid):
            return None
        # A bare name is one evaluation however often it is written, so the
        # pair cannot straddle two objects and needs no binding to name.
        # `this->`: the constructor's param shadows the member of the same name.
        src = f"(this->{escape_cpp_name(loop.iterable.name)})"
        return uid, [f"__for_it_{uid}.emplace({src}.begin());",
                     f"__for_end_{uid}.emplace({src}.end());"]

    def _emit_case(self, out: "TextIO", cfg: 'THIRResumableFrame',
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
        no_throw = entry_bb in cfg.no_throw_cases
        levels = [] if no_throw else self._unwind_levels(bb.region_stack)
        # Pre-allocate a `bool __fin_ran_N` guard name for each level whose
        # cleanup can run on an exit edge inside its C++ try and be re-run by
        # its own catch: a TryRegion with a finally, an ExceptRegion's parent
        # finally, or any WithRegion. The name is populated now so the
        # finally frames pushed just below share it (a return/break/continue
        # via _emit_finally_chain sets the same guard the level's catch
        # tests); the `bool` is declared later, before the level's `try {`.
        for i in levels:
            region = bb.region_stack[i]
            helper = (region.finally_helper_name
                      if isinstance(region, THIRTryRegion)
                      else region.parent_finally
                      if isinstance(region, THIRExceptRegion) else None)
            if helper is not None or isinstance(region, THIRWithRegion):
                self.ctx.finally_guard_counter += 1
                self.ctx.resumable_region_guards[region] = (
                    f"__fin_ran_{self.ctx.finally_guard_counter}")
        # Push FinallyContext entries so a `return` inside this case
        # body walks the right finally chain via _emit_finally_chain.
        # ExceptRegion contributes its parent try's finally because
        # Python runs finally after the handler completes.
        helpers = region_cleanups(bb.region_stack)
        pushed_finally = self._push_finally_helpers(helpers)
        # Pending-return ctx: when this case is inside the try
        # body or handler body of a CFG-based finally, install ctx
        # state so `return` inside the body routes through the
        # pending-return slot.
        prev_pending = self._snapshot_pending_return_ctx()
        self._install_pending_return_ctx(bb.region_stack, case_entries)
        base_level = self.ctx.indent_level
        try:
            # Buffer the body at the indent it will occupy inside all the
            # levels' tries, so we learn which guards actually get set before
            # declaring them: a region whose cleanup never runs on a normal
            # exit edge (e.g. a with-body that always raises) sets no guard,
            # so its `bool` + catch tests are skipped -- the shared
            # live_finally_guards gate.
            self.ctx.indent_level += len(levels)
            body_buf = io.StringIO()
            self._emit_case_body(body_buf, cfg, entry_bb, case_entries, func)
            self.ctx.indent_level = base_level
            text = body_buf.getvalue()
            # A body that only re-dispatches cannot throw: no region try.
            jump = self._redispatch_target(cfg, entry_bb, case_entries, func)
            if jump is not None and levels:
                levels = []
                text = _state_jump(body_indent, case_entries[jump].cpp_name())

            # Wrap the levels innermost first: a level's catches are emitted
            # after everything it encloses, so a guard an inner catch sets
            # (a `return` in a finally running the outer cleanups) is live by
            # the time this level declares its own.
            for depth in reversed(range(len(levels))):
                i = levels[depth]
                region = bb.region_stack[i]
                outer = bb.region_stack[:i]
                self.ctx.indent_level = base_level + depth
                ind = self.ctx.indent()
                catches = io.StringIO()
                catches.write(f"{ind}}}")
                if isinstance(region, THIRTryRegion):
                    self._emit_try_region_catches(
                        catches, ind, region, cfg, case_entries,
                        entry_bb, func, outer)
                elif isinstance(region, THIRWithRegion):
                    self._emit_with_region_catches(
                        catches, ind, region, case_entries)
                else:
                    self._emit_cleanup_level_catch(
                        catches, ind, region, outer, case_entries)
                catches.write("\n")
                guard = self._active_region_guard(region)
                decl = (f"{ind}bool {guard} = false;\n"
                        if guard is not None else "")
                text = f"{decl}{ind}try {{\n{text}{catches.getvalue()}"
            self.ctx.indent_level = base_level
            out.write(text)
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

    def _emit_cleanup_action(self, out: "TextIO", indent: str, action: tuple,
                             *, exc_val_cpp: str | None = None) -> None:
        """Emit one cleanup action (an entry of
        `_finally_helpers_for_region_stack`) on a normal or abandoning exit.
        `exc_val_cpp` is what a `with` `__exit__` that takes one receives."""
        tag, payload, _region = action
        if tag == "helper":
            self._emit_finally_helper_call(out, indent, payload)
        elif tag == "with":
            self._emit_with_exit(out, indent, payload, on_exception=False,
                                 exc_val_cpp=exc_val_cpp)
        else:
            for fname in payload:
                out.write(f"{indent}{fname}.reset();\n")

    def _active_region_guard(self, region: 'THIRRegion') -> 'str | None':
        """The region's guard name, but only if some exit edge in this case
        actually set it -- so a catch tests (and _emit_case declares) a guard
        only when it can fire. Returns None for a region whose cleanup never
        runs on a normal exit (the guard would be dead)."""
        guard = self.ctx.resumable_region_guards.get(region)
        if guard is not None and guard in self.ctx.live_finally_guards:
            return guard
        return None

    def _dtor_cleanup_cases(self, cfg: 'THIRResumableFrame') -> list[tuple[str, list]]:
        """Per suspended resume state, the cleanup actions (innermost-
        first) the frame destructor must run when the frame is dropped
        while suspended there -- the C++ analog of CPython closing a
        suspended generator (GeneratorExit running pending finallies and
        with.__exit__). States with nothing pending are omitted; when the
        result is empty no destructor is emitted at all.

        The selection is the frame's (`abandonment_cleanup`); CFG-based
        finallies (the finally body itself suspends) cannot run inside a
        destructor and are not in it; sema warns at the yield-in-finally
        site instead.
        """
        cases: list[tuple[str, list]] = []
        for index, actions in cfg.abandonment_cleanup:
            label = _StateLabel(_StateKind.RESUME, index)
            cases.append((label.cpp_name(), list(actions)))
        return cases

    def _emit_frame_dtor(self, out: "TextIO", struct_name: str,
                         dtor_cases: list[tuple[str, list]],
                         defer: str | None) -> None:
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
        if defer is not None:
            out.write(f"{inner}{defer}\n")
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
            return tuple((tag, payload if tag != "with" else id(payload))
                         for tag, payload, _region in actions)
        grouped: dict[tuple, tuple[list[str], list]] = {}
        for state_name, actions in dtor_cases:
            entry = grouped.setdefault(chain_key(actions), ([], actions))
            entry[0].append(state_name)
        for state_names, actions in grouped.values():
            for sn in state_names:
                out.write(f"{body}case {sn}:\n")
            for action in actions:
                self._emit_cleanup_action(out, action_ind, action,
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
            if isinstance(region, THIRTryRegion):
                if region.pending_return_flag is not None:
                    innermost = (region.pending_return_flag,
                                 region.pending_return_slot,
                                 region.finally_entry_bb,
                                 boundary)
                elif region.finally_helper_name is not None:
                    boundary += 1
            elif isinstance(region, THIRExceptRegion):
                if region.parent_pending_return_flag is not None:
                    innermost = (region.parent_pending_return_flag,
                                 region.parent_pending_return_slot,
                                 region.parent_finally_entry_bb,
                                 boundary)
                elif region.parent_finally is not None:
                    boundary += 1
            elif isinstance(region, THIRFinallyRegion):
                if region.pending_return_flag is not None:
                    innermost = (region.pending_return_flag,
                                 region.pending_return_slot,
                                 region.finally_exit_bb,
                                 boundary)
            elif isinstance(region, (THIRWithRegion, THIRLoopSourceRegion)):
                boundary += 1
        return innermost

    def _emit_finally_helper_call(self, out: "TextIO", indent: str,
                                    helper_name: str) -> None:
        """Emit `this->helper_name();`.

        The stop-check (`if __finally_stop`) is NOT emitted here: a normal
        exit checks it after all its cleanup ran
        (`_emit_generator_stop_check`), an exception path returns through
        the outer cleanups (`_emit_unwind_return`)."""
        out.write(f"{indent}this->{helper_name}();\n")

    def _emit_generator_stop_check(self, out: "TextIO", indent: str) -> None:
        """Emit `if (this->__finally_stop) { return StopIteration; }`.

        Only emitted when in generator shape and the struct has __finally_stop.
        Call AFTER all cleanup for a normal exit has run, so outer cleanups
        are never skipped by an early return."""
        if not (self._is_generator_shape() and self.ctx.generator_has_finally_stop):
            return
        done_state = self.ctx.generator_resumable_done_state or "S_DONE"
        out.write(f"{indent}if (this->__finally_stop) {{\n")
        out.write(f"{indent}{INDENT}__state = {done_state};\n")
        out.write(f"{indent}{INDENT}return ::tpy::make_unexpected("
                  f"::tpy::StopIteration{{}});\n")
        out.write(f"{indent}}}\n")

    def _emit_cancel_finally_stop(self, out: "TextIO", indent: str) -> None:
        """Where a state-machine handler catches (or a `with` suppresses):
        the exception came from a cleanup run after a `return` in a
        finally, which cancels that return, so the frame goes on."""
        if self._is_generator_shape() and self.ctx.generator_has_finally_stop:
            out.write(f"{indent}this->__finally_stop = false;\n")

    def _push_finally_helpers(self, helpers: list) -> int:
        """Push FinallyContext entries for each helper. Returns the
        count pushed for matching pop in a `finally:` clause.

        Each frame links to its source region's exit guard (looked up in
        `resumable_region_guards` from the region carried by the helper
        entry) so a return/break/continue via _emit_finally_chain sets the
        same `bool` the region's catch tests -- no double-run of a raising
        cleanup on the return path."""
        count = 0
        for action in helpers:
            region = action[2]
            def _emit_finally(o: "TextIO", ind: str, a=action) -> None:
                self._emit_cleanup_action(o, ind, a)
            guard = self.ctx.resumable_region_guards.get(region)
            fctx = FinallyContext(
                emit_finally=_emit_finally, terminates=False, loop_depth=0,
                guard_name=guard)
            self.ctx.finally_stack.append(fctx)
            count += 1
        return count

    @staticmethod
    def _unwind_levels(region_stack: tuple) -> list[int]:
        """The indexes of the regions of a case's stack that open a C++
        `try` level of their own around the case body: every TryRegion and
        WithRegion, every ExceptRegion whose try has a helper finally, and
        -- inside an outer level -- every LoopSourceRegion (outside all
        levels an escaping exception leaves its fields to the frame's
        destruction, BUGS.md#finished-frame-keeps-locals). One level per cleanup
        action, nested as the regions nest, is what gives an action that
        raises CPython's unwind: its exception leaves the catch clause
        that ran it and is dispatched, as a fresh exception, to the
        handlers, `__exit__`s and finallies of the levels outside it."""
        levels: list[int] = []
        for i, r in enumerate(region_stack):
            if (isinstance(r, (THIRTryRegion, THIRWithRegion))
                    or (isinstance(r, THIRExceptRegion)
                        and r.parent_finally is not None)
                    or (isinstance(r, THIRLoopSourceRegion)
                        and levels and r.fields)):
                levels.append(i)
        return levels

    def _install_pending_return_ctx(
            self, region_stack: tuple,
            case_entries: dict[int, _StateLabel]) -> None:
        """Route a `return` emitted for `region_stack` through the innermost
        CFG-based finally's pending-return slot, or directly when none."""
        pending = self._pending_return_info_for_region_stack(region_stack)
        if pending is None:
            self._restore_pending_return_ctx((None, None, None, 0))
            return
        flag, slot, finally_entry_bb, boundary = pending
        self._restore_pending_return_ctx(
            (flag, slot, case_entries[finally_entry_bb].cpp_name(), boundary))

    def _emit_cleanup_level_catch(self, out: "TextIO", indent: str,
                                  region: 'THIRRegion', outer: tuple,
                                  case_entries: dict[int, _StateLabel]
                                  ) -> None:
        """The catch-all of a level that only cleans up (an ExceptRegion's
        parent finally, a loop-source release): run the action, rethrow.
        A helper whose normal-exit copy already ran in this case (its
        guard) is not run again."""
        [(tag, payload, _region)] = region_cleanups(
            (region,))
        out.write(" catch (...) {\n")
        self.ctx.indent_level += 1
        catch_ind = self.ctx.indent()
        if tag == "reset":
            for fname in payload:
                out.write(f"{catch_ind}{fname}.reset();\n")
        else:
            guard = self._active_region_guard(region)
            ind = catch_ind
            if guard is not None:
                out.write(f"{catch_ind}if (!{guard}) {{\n")
                ind = catch_ind + INDENT
            self._emit_finally_helper_call(out, ind, payload)
            self._emit_unwind_return(out, ind, outer, case_entries)
            if guard is not None:
                out.write(f"{catch_ind}}}\n")
        out.write(f"{catch_ind}throw;\n")
        self.ctx.indent_level -= 1
        out.write(f"{indent}}}")

    def _emit_unwind_return(self, out: "TextIO", indent: str, outer: tuple,
                            case_entries: dict[int, _StateLabel]) -> None:
        """After a finally helper ran on an exception path: a `return` in it
        (`__finally_stop`) cancels the exception and returns from the
        finally's position -- the cleanups of the `outer` regions run as
        for any `return` there, `__exit__` seeing no exception."""
        if not (self._is_generator_shape()
                and self.ctx.generator_has_finally_stop):
            return
        saved_stack = self.ctx.finally_stack
        saved_pending = self._snapshot_pending_return_ctx()
        self.ctx.finally_stack = []
        try:
            self._push_finally_helpers(
                region_cleanups(outer))
            self._install_pending_return_ctx(outer, case_entries)
            out.write(f"{indent}if (this->__finally_stop) {{\n")
            if self.ctx.async_pending_return_flag is not None:
                # The return parks into a suspending finally, whose own
                # normal exits test the flag: cleared so they do not stop
                # before that finally has run.
                out.write(f"{indent}{INDENT}this->__finally_stop = false;\n")
            out.write(self._generator_stop_return(indent + INDENT))
            out.write(f"{indent}}}\n")
        finally:
            self.ctx.finally_stack = saved_stack
            self._restore_pending_return_ctx(saved_pending)

    def _emit_with_region_catches(self, out: "TextIO", indent: str,
                                    region: 'THIRWithRegion',
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
                self._emit_cancel_finally_stop(out, catch_ind)
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
                                  region: 'THIRTryRegion',
                                  cfg: 'THIRResumableFrame',
                                  case_entries: dict[int, _StateLabel],
                                  case_entry_bb: int,
                                  func: TpyFunction,
                                  outer: tuple) -> None:
        """Emit `} catch (...) { ... }` clauses for a TryRegion at the
        close of a case body. Each handler's body is emitted inline
        inside its catch (walks the handler entry BB).

        `case_entry_bb` is the case-entry BB this region wraps; used to
        determine which __sub_<n> to reset (the in-flight sub-future
        for this resume case).

        `outer` is the case's region stack outside this TryRegion: a
        `return` in this try's finally, run on an exception path, runs
        their cleanups on its way out (`_emit_unwind_return`)."""
        yield_for_case = self._yield_at_resume(cfg, case_entry_bb)
        # Helpers to invoke on throw from inside the handler body: this
        # region's own finally (the inner ones ran on entry to the handler).
        handler_throw_finallies: tuple[str, ...] = ()
        if region.finally_helper_name is not None:
            handler_throw_finallies = (region.finally_helper_name,)
        own_guard = self._active_region_guard(region)
        for handler in region.handlers:
            emit_prims.emit_except_handler_header(self.ctx, out, handler)
            self.ctx.indent_level += 1
            catch_indent = self.ctx.indent()
            # Reset the in-flight sub-future first action in catch.
            # Generators have no sub-futures; only reset for async shape.
            if (yield_for_case is not None
                    and isinstance(yield_for_case.payload, THIRAwaitSuspension)):
                self._emit_sub_reset(out, catch_indent,
                                      yield_for_case.payload,
                                      yield_for_case.suspension_index)
            # Once this try's finally has run in the case, an exception is
            # that finally's own or comes from after the try statement:
            # never one of this try's handlers to catch.
            if own_guard is not None:
                out.write(f"{catch_indent}if ({own_guard}) throw;\n")
            self._emit_cancel_finally_stop(out, catch_indent)
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
            entries = cfg.handler_entries.get(region)
            handler_entry: int | None = (entries.get(handler)
                                         if entries is not None else None)
            handler_stack_helpers: list = []
            saved_guards: IdentityMap = IdentityMap()
            if handler_entry is not None:
                handler_bb = cfg.blocks[handler_entry]
                handler_stack_helpers = region_cleanups(
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
                    self._emit_unwind_return(out, body_ic, outer,
                                             case_entries)
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
                    self._emit_unwind_return(out, inner_catch, outer,
                                             case_entries)
                    out.write(f"{inner_catch}throw;\n")
                self.ctx.indent_level -= 1
                out.write(f"{inner_close}}}\n")
            self.ctx.indent_level -= 1
            out.write(f"{indent}}}")
        # Catch-all: reset sub. For helper-based finally: call helper,
        # re-throw. For
        # CFG-based finally: save current exception to the
        # captured-exc field and transition state to the finally entry
        # so the finally body runs in the state machine (with possible
        # suspensions); the saved exception is rethrown by the
        # AsyncFinallyExit stmt at the finally tail.
        out.write(" catch (...) {\n")
        self.ctx.indent_level += 1
        catch_indent = self.ctx.indent()
        if (yield_for_case is not None
                and isinstance(yield_for_case.payload, THIRAwaitSuspension)):
            self._emit_sub_reset(out, catch_indent,
                                  yield_for_case.payload,
                                  yield_for_case.suspension_index)
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
                self._emit_unwind_return(out, self.ctx.indent(), outer,
                                         case_entries)
                self.ctx.indent_level -= 1
                out.write(f"{catch_indent}}}\n")
                out.write(f"{catch_indent}throw;\n")
            else:
                if region.finally_helper_name is not None:
                    self._emit_finally_helper_call(
                        out, catch_indent, region.finally_helper_name)
                    self._emit_unwind_return(out, catch_indent, outer,
                                             case_entries)
                out.write(f"{catch_indent}throw;\n")
        self.ctx.indent_level -= 1
        out.write(f"{indent}}}")

    def _yield_at_resume(self, cfg: 'THIRResumableFrame',
                          resume_bb: int) -> 'THIRSuspend | None':
        return self._resume_to_yield(cfg).get(resume_bb)

    def _emit_case_body(self, out: "TextIO", cfg: 'THIRResumableFrame',
                         entry_bb: int, case_entries: dict[int, _StateLabel],
                         func: TpyFunction, *, tail: bool = False) -> None:
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
            if y.payload.kind is AwaitKind.RETURN:
                # Resume core already emitted the return.
                return
        # A case may be (re-)entered after a suspension that split a
        # narrowed region; re-establish the narrowed bindings active on
        # entry before walking, so post-suspension member access sees the
        # narrowed type rather than the raw frame field.
        entry = cfg.blocks[entry_bb].entry_narrowings
        narrow_buf = io.StringIO()
        tok = self._emit_resume_narrowings(narrow_buf, entry)
        walk_buf = io.StringIO()
        self._walk_inline(walk_buf, cfg, entry_bb, case_entries, func,
                          chain_entry=entry, tail=tail)
        self._restore_resume_narrowings(tok)
        # A body that only re-dispatches reads none of the re-established aliases.
        if self._redispatch_target(cfg, entry_bb, case_entries, func) is None:
            out.write(narrow_buf.getvalue())
        out.write(walk_buf.getvalue())

    @staticmethod
    def _exited_regions(from_regions: tuple, to_regions: tuple) -> list:
        common = 0
        while (common < len(from_regions) and common < len(to_regions)
               and from_regions[common] is to_regions[common]):
            common += 1
        return list(from_regions[common:])

    def _region_exit_emits(self, from_regions: tuple, to_regions: tuple) -> bool:
        """Whether `_emit_exit_region_finallies` writes anything on this edge."""
        if not from_regions:
            return False
        if region_cleanups(
                tuple(self._exited_regions(from_regions, to_regions))):
            return True
        return (not _regions_have_pending_cleanup(to_regions)
                and self._is_generator_shape()
                and self.ctx.generator_has_finally_stop)

    def _redispatch_target(self, cfg: 'THIRResumableFrame', entry_bb: int,
                           case_entries: dict[int, _StateLabel],
                           func: TpyFunction) -> 'int | None':
        """The case entry the case at `entry_bb` hands control to when that is
        ALL it does -- it emits no statement, re-establishes nothing it reads,
        and leaves no region with cleanup -- else None. Such a case can share
        its target's label, cannot throw, and reads no narrowed alias."""
        if entry_bb not in self._print_state(cfg).redispatch:
            self._print_state(cfg).redispatch[entry_bb] = self._find_redispatch_target(
                cfg, entry_bb, case_entries, func)
        return self._print_state(cfg).redispatch[entry_bb]

    def _find_redispatch_target(self, cfg: 'THIRResumableFrame', entry_bb: int,
                                case_entries: dict[int, _StateLabel],
                                func: TpyFunction) -> 'int | None':
        is_async = not self._is_generator_shape()
        if is_async and self._yield_at_resume(cfg, entry_bb) is not None:
            return None     # an async resume polls its sub-future first
        seen = {entry_bb}
        cur = entry_bb
        while True:
            bb = cfg.blocks[cur]
            if not all(self._stmt_emits_nothing(st, func) for st in bb.stmts):
                return None
            t = bb.terminator
            # An await of a coroutine bound earlier constructs nothing: its
            # suspend step is the transfer to its resume case.
            if (is_async and isinstance(t, THIRSuspend)
                    and t.payload.prebuilt_slot is not None):
                return t.resume_bb
            if not isinstance(t, THIRFall):
                return None
            nxt = t.next_bb
            if nxt in case_entries:
                if self._region_exit_emits(
                        bb.region_stack, cfg.blocks[nxt].region_stack):
                    return None
                return nxt
            if nxt in seen:
                return None
            seen.add(nxt)
            cur = nxt

    def _stmt_emits_nothing(self, stmt: object, func: TpyFunction) -> bool:
        """A block statement the walker writes no code for: a leaf that
        lowered to nothing (`pass`, a docstring), or the setup of a loop whose
        source IS its iterator and needs no stash."""
        if isinstance(stmt, THIRForIterSetup):
            if stmt.is_async:
                return False
            info = self._for_info(func, stmt.uid)
            return (info is not None and info.strategy == "next"
                    and not self._for_has_src_slot(info, stmt.uid))
        if isinstance(stmt, (THIRWithEnter, THIRAsyncWithSetup,
                             THIRFinallyExit)):
            return False
        return self._leaf.leaf_emits_nothing(stmt)

    @staticmethod
    def _for_has_src_slot(info: 'GeneratorForInfo', uid: int) -> bool:
        return any(fn == f"__for_src_{uid}" for fn, _ in info.fields)

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
        exited = self._exited_regions(from_regions, to_regions)
        guards = self.ctx.resumable_region_guards
        # Innermost first. Leaving an except handler normally runs the parent
        # try's finally (Python semantics); leaving a with-region normally is
        # `__exit__(None, None, None)`.
        for action in reversed(
                region_cleanups(tuple(exited))):
            # Set the region's guard (if its C++ try is still open in this
            # case) before its cleanup copy runs, so the region's own catch
            # skips re-running a copy that raised.
            guard = guards.get(action[2])
            if guard is not None:
                self.ctx.live_finally_guards.add(guard)
                out.write(f"{indent}{guard} = true;\n")
            self._emit_cleanup_action(out, indent, action)
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
            out, delta, self._leaf.narrow_aliases,
            self._leaf.published.resume_extractions, indent_extra=0)
        return (nv_saved, proto_snap, lit_snap)

    def _restore_resume_narrowings(self, token: '_NarrowingToken | None') -> None:
        if token is None:
            return
        nv_saved, proto_snap, lit_snap = token
        self.ctx.restore_narrowed_vars(nv_saved)
        self.ctx.restore_protocol_narrowings(proto_snap)
        self.ctx.restore_literal_facts(lit_snap)

    def _walk_inline(self, out: "TextIO", cfg: 'THIRResumableFrame',
                     start_bb: int, case_entries: dict[int, _StateLabel],
                     func: TpyFunction,
                     chain_entry: 'dict[str, TpyType] | None' = None,
                     *, tail: bool = False) -> None:
        """Walk BBs starting from start_bb, emitting their statements
        and following Fall/Branch terminators inline. Stops when the
        terminator is Yield/Return/Raise/Unreachable, or when a
        Fall/Branch target is a case_entry (then emits a state
        transition).

        `chain_entry` is the narrowing-fact set already bound in this C++
        scope (the entry_narrowings of start_bb, which every inline
        Fall-successor shares); branch arms diff their own facts against
        it so enclosing narrowings aren't re-cast. `tail`: nothing follows
        this walk in the enclosing loop body (the loop form only)."""
        chain_entry = chain_entry or {}
        body_indent = self.ctx.indent()
        cur = start_bb
        while True:
            bb = cfg.blocks[cur]
            # Emit BB statements.
            for stmt in bb.stmts:
                if isinstance(stmt, THIRForIterSetup):
                    self._emit_async_for_iter_setup(out, body_indent, stmt, func)
                elif isinstance(stmt, THIRWithEnter):
                    self._emit_with_enter(out, body_indent, stmt, func)
                elif isinstance(stmt, THIRAsyncWithSetup):
                    self._emit_async_with_setup(out, body_indent, stmt)
                elif isinstance(stmt, THIRFinallyExit):
                    self._emit_async_finally_exit(out, body_indent, stmt)
                else:
                    self._leaf.emit_leaf_stmt(
                        out, stmt, self.ctx.indent_level)
            t = bb.terminator
            if isinstance(t, THIRSuspend):
                self._emit_yield_terminator(out, body_indent, t, func)
                return
            if isinstance(t, THIRFrameReturn):
                # The return maker walks the active finally chain
                # (ctx.finally_stack) before emitting the Poll<T>::ready(...);
                # a routed body swaps only the VALUE render inside that
                # scaffolding (_async_return_value_cpp). The surrounding
                # comment/temp emission mirrors the linear statement walk --
                # minus its top-level line tracking, which is module-init
                # state and module init never emits a resumable frame.
                stmt_indent = self.ctx.indent()
                code = self._resumable_return_code(self._leaf.leaf(t.slot),
                                                   stmt_indent)
                # The maker's renders can queue temps; they belong ahead of
                # the code that reads them, so flush only after it is built.
                self.ctx.temps.flush(out, stmt_indent)
                out.write(code)
                return
            if isinstance(t, THIRFrameRaise):
                # Emit the raise as an ordinary TpyRaise statement; the
                # finally chain is run via C++ exception unwinding.
                self._leaf.emit_leaf_stmt(
                    out, t.slot, self.ctx.indent_level)
                return
            if isinstance(t, THIRUnreachable):
                self._emit_unreachable_tail(out, body_indent, func)
                return
            if isinstance(t, THIRFall):
                if t.next_bb in case_entries:
                    self._emit_exit_region_finallies(
                        out, body_indent,
                        cfg.blocks[cur].region_stack,
                        cfg.blocks[t.next_bb].region_stack)
                    out.write(self._transfer(
                        body_indent, case_entries[t.next_bb].cpp_name(),
                        tail=tail))
                    return
                cur = t.next_bb
                continue
            if isinstance(t, THIRBranch):
                cond_cpp = self._leaf.render_expr(t.cond)
                self.ctx.temps.flush(out, body_indent)
                out.write(f"{body_indent}if ({cond_cpp}) {{\n")
                self.ctx.indent_level += 1
                self._walk_inline_or_jump(out, cfg, t.then_bb, case_entries,
                                            func, from_bb=cur,
                                            outer_narrowings=chain_entry,
                                            tail=tail)
                self.ctx.indent_level -= 1
                else_buf = io.StringIO()
                self.ctx.indent_level += 1
                self._walk_inline_or_jump(else_buf, cfg, t.else_bb, case_entries,
                                            func, from_bb=cur,
                                            outer_narrowings=chain_entry,
                                            tail=tail)
                self.ctx.indent_level -= 1
                # The loop form's else arm at the end of the loop body can
                # come out empty (a filter that failed: just go round).
                if else_buf.getvalue() or self._loop_form is None:
                    out.write(f"{body_indent}}} else {{\n")
                    out.write(else_buf.getvalue())
                out.write(f"{body_indent}}}\n")
                return
            if isinstance(t, THIRForAdvance):
                self._emit_async_for_advance(
                    out, body_indent, cfg, t, case_entries, func, from_bb=cur,
                    tail=tail)
                return
            if isinstance(t, THIRMatchDispatch):
                self._emit_match_dispatch(
                    out, cfg, t, case_entries, func, from_bb=cur)
                return
            raise CodeGenError(
                f"internal: unknown terminator {type(t).__name__}",
                loc=None)

    def _emit_match_dispatch(self, out: "TextIO", cfg: 'THIRResumableFrame',
                              t: 'THIRMatchDispatch',
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
        arm_bb_by_case = IdentityMap()
        for case, arm_bb in zip(t.match_stmt.cases, t.arm_bbs):
            arm_bb_by_case[case] = arm_bb

        def arm_hook(case: 'TpyMatchCase', lvl: int) -> None:
            # The tier hands the arm-body depth; the walker's renders key off
            # ctx.indent_level, so scope it to the arm.
            old_level = self.ctx.indent_level
            self.ctx.indent_level = lvl
            try:
                emit_arm(arm_bb_by_case[case])
            finally:
                self.ctx.indent_level = old_level
        self._leaf.emit_match_dispatch(out, t.slot,
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

    def _walk_inline_or_jump(self, out: "TextIO", cfg: 'THIRResumableFrame',
                              target_bb: int,
                              case_entries: dict[int, _StateLabel],
                              func: TpyFunction,
                              from_bb: int,
                              outer_narrowings: 'dict[str, TpyType] | None' = None,
                              *, tail: bool = False) -> None:
        body_indent = self.ctx.indent()
        if target_bb in case_entries:
            # The target is its own case; it re-establishes narrowings from
            # its own entry_narrowings (it may be re-entered after a
            # suspension, so the binding can't be carried via this edge).
            self._emit_exit_region_finallies(
                out, body_indent,
                cfg.blocks[from_bb].region_stack,
                cfg.blocks[target_bb].region_stack)
            out.write(self._transfer(
                body_indent, case_entries[target_bb].cpp_name(), tail=tail))
        else:
            entry = cfg.blocks[target_bb].entry_narrowings
            tok = self._emit_resume_narrowings(out, entry,
                                               outer=outer_narrowings)
            self._walk_inline(out, cfg, target_bb, case_entries, func,
                              chain_entry=entry, tail=tail)
            self._restore_resume_narrowings(tok)

    def _emit_async_finally_exit(self, out: "TextIO", indent: str,
                                   stmt: 'THIRFinallyExit') -> None:
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
                         stmt: 'THIRWithEnter', func: TpyFunction) -> None:
        """Emit the with-stmt setup sequence (manager bind via
        `_emit_with_ctx_bind`, then the `__enter__` call):
            <target> = (*__with_ctx_<n>).__enter__();   # if target
            (*__with_ctx_<n>).__enter__();              # else
        A pointer-form target takes the THIR with's bind
        (`frame_with_pointer_bind`).
        """
        from ..thir.emit import frame_with_pointer_bind
        ctx_n = stmt.ctx_n
        item = stmt.item
        self._emit_with_ctx_bind(out, indent, stmt)
        if item.target is not None:
            target = escape_cpp_name(item.target)
            enter_call = f"(*__with_ctx_{ctx_n}).__enter__()"
            if self._frame_of(func).pointer_with_target(item.target):
                slot = self._frame_of(func).ptr_slot_field(item)
                out.write(f"{indent}" + frame_with_pointer_bind(
                    target, enter_call,
                    slot.name if slot is not None else None) + "\n")
            elif self._frame_of(func).slot_backed(item.target):
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
                                 stmt: 'THIRAsyncWithSetup') -> None:
        """Populate the async-with `__with_ctx_<n>` frame field. Subsequent
        Yield BBs (aenter/aexit) emplace `__sub_<i>` with
        `(*__with_ctx_<n>, ...)`."""
        self._emit_with_ctx_bind(out, indent, stmt)

    def _emit_with_ctx_bind(
            self, out: "TextIO", indent: str,
            stmt: 'THIRWithEnter | THIRAsyncWithSetup') -> None:
        """Populate the `__with_ctx_<n>` frame field for a (sync or async)
        with-region: emplace an owned `frame_slot<CM>` manager (whose
        `operator=` is deleted), or bind a borrowed `CM*`. A global manager
        already renders as `CM*`, so the borrowed bind must not re-take its
        address (would double-pointer)."""
        ctx_n, item = stmt.ctx_n, stmt.item
        ctx_expr = self._leaf.render_expr(stmt.manager)
        self.ctx.temps.flush(out, indent)
        if item.manager_borrowed:
            if stmt.manager in self._leaf.published.pointer_sources:
                out.write(f"{indent}__with_ctx_{ctx_n} = {ctx_expr};\n")
            else:
                out.write(f"{indent}__with_ctx_{ctx_n} = &({ctx_expr});\n")
        else:
            out.write(f"{indent}__with_ctx_{ctx_n}.emplace({ctx_expr});\n")

    def _emit_with_exit(self, out: "TextIO", indent: str,
                               region: 'THIRWithRegion',
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
        return self._frame_of(func).for_info_by_uid.get(uid)

    def _for_src_store(self, out: "TextIO", indent: str,
                       setup: 'THIRForIterSetup', uid: int,
                       info: 'GeneratorForInfo') -> 'tuple[str | None, str]':
        """Move an RVALUE for-loop source into its `__for_src_<uid>` frame
        slot. Returns `(slot access or None, the source render)`; None means
        the source is an lvalue and the frame holds nothing.

        The slot is the frame's ownership of the iteration: a fresh value has
        no other owner, so anything the loop lends out of its elements would
        dangle at the first suspension without it."""
        # The leaf hands over a BARE render for every narrowed-optional
        # iterable it admits; the lowering decides this unwrap.
        src_cpp = self._leaf.render_expr(setup.iterable)
        if setup.iterable in self._leaf.published.unwrapped_sources:
            src_cpp = f"(*{src_cpp})"
        self.ctx.temps.flush(out, indent)
        if not self._for_has_src_slot(info, uid):
            return (None, src_cpp)
        emplace = emit_prims.for_src_emplace(f"__for_src_{uid}", src_cpp)
        out.write(f"{indent}{emplace}\n")
        return (f"(*__for_src_{uid})", src_cpp)

    def _for_src_access(self, out: "TextIO", indent: str,
                        setup: 'THIRForIterSetup', uid: int,
                        info: 'GeneratorForInfo') -> str:
        """A spelling of the for-loop source that may be READ MORE THAN ONCE,
        naming a single evaluation.

        `begin()` and `end()` both come off it: an impure accessor runs once,
        as CPython runs it, and the pair cannot straddle two different
        objects. An rvalue is named by its frame slot, an lvalue by a
        block-local reference binding -- not by a `frame_slot<T&>`, whose
        stored pointer would name a sibling frame field and be invalidated by
        a frame move. A caller that reads the source ONCE takes
        `_for_src_store`'s own spelling and needs no binding."""
        slot, src_cpp = self._for_src_store(out, indent, setup, uid, info)
        if slot is not None:
            return slot
        name = f"__for_obj_{uid}"
        out.write(f"{indent}auto& {name} = {src_cpp};\n")
        return name

    def _emit_async_for_iter_setup(self, out: "TextIO", indent: str,
                                    stmt: 'THIRForIterSetup',
                                    func: TpyFunction) -> None:
        """Initialize for-loop iteration state into the frame, per strategy
        (range / begin_end generators keep their fast shape):
          async_for: `__for_itr.emplace((it).__aiter__())`
          range:     `__for_i`/`__for_stop`[/`__for_step`] counters
          begin_end: `[__for_src.emplace(...);] __for_it.emplace(src.begin()); __for_end.emplace(...end())`
          next:      `[__for_src.emplace(...);]` (the source IS the iterator)
          iter_next: `::tpy::resumable_iter_init(__for_itr, src)` (owns the
                     derived iterator, or no-ops for a self-iterator source)
        """
        uid = stmt.uid
        # The source loop's setup runs once per frame, so a pair the
        # constructor already took is not taken again.
        if self._print_state(self._frame_of(func)).ctor_seeded_uid == uid:
            return
        if stmt.is_async:
            iter_cpp = self._leaf.render_expr(stmt.iterable)
            self.ctx.temps.flush(out, indent)
            # A global iterable already renders as `Src*`; deref so the
            # `.__aiter__()` call resolves rather than hitting `.`-on-pointer.
            if stmt.iterable in self._leaf.published.pointer_sources:
                iter_cpp = f"*({iter_cpp})"
            out.write(f"{indent}__for_itr_{uid}.emplace(({iter_cpp}).__aiter__());\n")
            return
        info = self._for_info(func, uid)
        strat = info.strategy if info else "iter_next"
        if strat == "range":
            self._emit_for_range_setup(out, indent, stmt, uid)
        elif strat == "begin_end":
            src = self._for_src_access(out, indent, stmt, uid, info)
            out.write(f"{indent}__for_it_{uid}.emplace(({src}).begin());\n")
            out.write(f"{indent}__for_end_{uid}.emplace(({src}).end());\n")
        elif strat == "next":
            # Source IS the iterator, and the advance names it again -- so
            # only the frame's ownership of an rvalue is settled here; an
            # lvalue needs no binding, nothing reads one
            # (`_stmt_emits_nothing` counts on that, so a named source must
            # not even reach the render).
            if self._for_has_src_slot(info, uid):
                self._for_src_store(out, indent, stmt, uid, info)
        else:  # iter_next (universal)
            # A temporary source is stashed in `__for_src` first because
            # `tpy::__iter__` borrows its argument (it would otherwise dangle).
            # The init/next go through resumable_iter_* so a self-iterator
            # source (a move-only generator) is driven in place instead of
            # copied into the iterator slot -- see generator.hpp.
            # One read, so no binding: the render already names one
            # evaluation.
            slot, src_cpp = self._for_src_store(out, indent, stmt, uid, info)
            src = slot if slot is not None else src_cpp
            out.write(f"{indent}::tpy::resumable_iter_init(__for_itr_{uid}, {src});\n")

    def _emit_for_range_setup(self, out: "TextIO", indent: str,
                              stmt: 'THIRForIterSetup', uid: int) -> None:
        """range() counter init into the frame counters."""
        range_call = stmt.source
        elem_type = stmt.elem_type
        if elem_type and isinstance(elem_type, IntLiteralType):
            elem_type = self.ctx.analyzer.ctx.default_int_type
        cpp_elem = self.types.type_to_cpp(elem_type) if elem_type else "int32_t"
        gen_args = [self._leaf.render_expr(b) for b in stmt.bounds]
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
                           t: 'THIRForAdvance'
                           ) -> tuple[str, str, str, list[str]]:
        """Return (pre, exhausted_test, live_test, bind_post) for the
        AsyncForAdvance, per strategy. `pre` runs before the exhaustion test;
        the loop exits when `exhausted_test` is true and goes on while
        `live_test` is (the same test from the other side, for a loop that
        takes it as its condition); `bind_post` is the list of statements that
        bind the loop var (and advance the cursor) on the live path."""
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
            return ("", f"!({cont})", cont, bind_post)
        if strat == "begin_end":
            it, end = f"(*__for_it_{uid})", f"(*__for_end_{uid})"
            if info.borrow_tuple_loop_var == stmt.var:
                # Proxy-ref tuple element (dict_items): the prvalue proxy is
                # held for the bind and lent element by element as the
                # borrow-form tuple; element refs are stable.
                step = f"__for_step_{uid}"
                lent = self._leaf.render_loop_step(t.step, step)
                # One line: the bind counts toward the inline-`__next__` size.
                bind_post = [f"{{ auto&& {step} = *({it})++; "
                             f"{cpp_var} = {lent}; }}"]
            elif info.pointer_form_loop_var == stmt.var:
                bind_post = [f"{cpp_var} = &(*({it})++);"]
            elif info.opt_ptr_loop_var == stmt.var:
                bind_post = [f"{cpp_var} = ::tpy::optional_to_ptr(*({it})++);"]
            else:
                bind_post = [f"{cpp_var} = *({it})++;"]
            return ("", f"{it} == {end}", f"!({it} == {end})", bind_post)
        # next / iter_next: __next__() into __for_r, exhaust on !has_value.
        r = f"(*__for_r_{uid})"
        if strat == "next":
            src = self._for_src_expr(t, info)
            pre = f"__for_r_{uid}.emplace({src}.__next__());"
        else:  # iter_next
            # resumable_iter_next drives the owned iterator, or the self-iterator
            # source in place -- both re-read `src` so a frame move never dangles.
            src = self._for_src_expr(t, info)
            pre = (f"__for_r_{uid}.emplace("
                   f"::tpy::resumable_iter_next(__for_itr_{uid}, {src}));")
        # A source-derived field takes one spelling whichever form the trait
        # picked: `unwrap_ref_move` hands `frame_slot` either a reference to bind
        # or a value to move in, and `emplace` resolves to the right one.
        if info is not None and info.loop_var_field is not None \
                and info.loop_var_field[0] == stmt.var:
            return (pre, f"!{r}.has_value()", f"{r}.has_value()",
                    [f"{cpp_var}.emplace(::tpy::unwrap_ref_move(*{r}));"])
        elem = f"::tpy::unwrap_ref(*{r})"
        # Bind form mirrors the loop var's frame storage shape:
        # pointer-form `T*` (alias), frame_slot `.emplace`, or value assign.
        if info is not None and info.pointer_form_loop_var == stmt.var:
            bind_post = [f"{cpp_var} = &({elem});"]
        elif info is not None and info.opt_ptr_loop_var == stmt.var:
            bind_post = [f"{cpp_var} = ::tpy::optional_to_ptr({elem});"]
        elif self._frame_of(func).slot_backed(stmt.var):
            bind_post = [f"{cpp_var}.emplace({elem});"]
        else:
            bind_post = [f"{cpp_var} = {elem};"]
        return (pre, f"!{r}.has_value()", f"{r}.has_value()", bind_post)

    def _for_src_expr(self, t: 'THIRForAdvance',
                      info: 'GeneratorForInfo') -> str:
        """The iterator source for the `next` strategy advance: the stored
        `__for_src` (temporary) or the re-referencable named expression."""
        uid = t.uid
        if self._for_has_src_slot(info, uid):
            return f"(*__for_src_{uid})"
        return self._leaf.render_expr(t.iterable)

    def _emit_async_for_advance(self, out: "TextIO", indent: str,
                                  cfg: 'THIRResumableFrame',
                                  t: 'THIRForAdvance',
                                  case_entries: dict[int, _StateLabel],
                                  func: TpyFunction,
                                  from_bb: int, *, tail: bool = False) -> None:
        """Emit the per-iteration advance for an AsyncForAdvance terminator
        (strategy-specific check + bind), then transfer to the body
        (`has_value_bb`) or loop exit (`exhausted_bb`)."""
        pre, exhausted_test, live_test, bind_post = self._for_advance_parts(func, t)
        form = self._loop_form
        if form is not None and form.may_hoist_test and not pre:
            form.condition = live_test
            for line in bind_post:
                out.write(f"{indent}{line}\n")
            self._walk_inline_or_jump(
                out, cfg, t.has_value_bb, case_entries, func, from_bb=from_bb,
                tail=tail)
            return
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
            out.write(self._transfer(
                inner, case_entries[t.exhausted_bb].cpp_name(), tail=False))
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
            out, cfg, t.has_value_bb, case_entries, func, from_bb=from_bb,
            tail=tail)

    def _emit_unreachable_tail(self, out: "TextIO", indent: str,
                                 func: TpyFunction) -> None:
        """Tail emission for a BB whose end is statically unreachable
        (no explicit return/raise). Generators that fall off the end stop
        iterating (StopIteration). For void async defs, emit Ready(unit);
        otherwise panic."""
        if self._is_generator_shape():
            # Fell off the end of the generator body -> StopIteration.
            emit_prims.emit_finally_chain(self.ctx, out, indent)
            if self._loop_form is None or self._loop_form.records_done:
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
    def _sub_slot_name(payload: 'THIRAwaitSuspension',
                       suspension_index: int) -> str:
        """The C++ slot a suspension polls/resets: the dedicated
        `__sub_<i>` field, or -- for a prebuilt-slot await of a bound
        coroutine -- the handle's own frame field."""
        if payload.prebuilt_slot is not None:
            return escape_cpp_name(payload.prebuilt_slot.name)
        return AsyncCoroCodegen._sub_field_name(suspension_index)

    def _emplace_args(self, payload: 'THIRAwaitSuspension') -> 'list[str]':
        """All emplace args for a sub-coro construction -- the leaf seam's
        argument chokepoint."""
        return self._leaf.render_await_args(payload.args)

    def _suspend_expr_cpp(self, slot: 'THIRLeafSlot | None') -> str:
        """Render a bound-method await receiver or an ERASED/BORROWED await
        operand -- the THIR leaf seam's suspend-expression chokepoint (the
        skeleton keeps its move / & / .get() / __self-prepend wrap around
        this render)."""
        return self._leaf.render_expr(slot)

    def _emit_sub_reset(self, out: "TextIO", indent: str,
                        payload: 'THIRAwaitSuspension',
                        suspension_index: int) -> None:
        sub = self._sub_slot_name(payload, suspension_index)
        if payload.mode is AwaitMode.BORROWED:
            out.write(f"{indent}{sub} = nullptr;\n")
        elif (payload.mode is AwaitMode.INLINE
              or payload.mode is AwaitMode.ERASED):
            out.write(f"{indent}{sub}.reset();\n")
        else:
            raise CodeGenError(f"unknown await mode {payload.mode!r}",
                               loc=None)

    def _emit_resume_core(self, out: "TextIO", indent: str,
                          payload: 'THIRAwaitSuspension',
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
        if (payload.kind is AwaitKind.ASSIGN
                or payload.kind is AwaitKind.VARDECL):
            target = escape_cpp_name(payload.bind_target)
            if self._frame_of(func).slot_backed(payload.bind_target):
                out.write(f"{indent}{target}.emplace({moved});\n")
            else:
                out.write(f"{indent}{target} = {moved};\n")
        elif payload.kind is AwaitKind.RETURN:
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
        elif payload.kind is AwaitKind.DISCARD:
            out.write(f"{indent}(void){moved};\n")
        else:
            raise CodeGenError(f"unknown await kind {payload.kind!r}",
                               loc=None)
        self._emit_sub_reset(out, indent, payload, suspension_index)

    def _emit_yield_terminator(self, out: "TextIO", indent: str,
                               t: 'THIRSuspend', func: TpyFunction) -> None:
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
                              t: 'THIRSuspend') -> None:
        """Generator suspension: compute the yielded value, advance to the
        resume state, and return it to the caller. The next `__next__()`
        call re-enters the switch at the resume state, whose case header
        is a no-op (no poll/bind) and simply continues after the yield."""
        payload = t.payload
        assert isinstance(payload, THIRYieldSuspension), \
            "generator shape requires a YieldPayload terminator"
        yield_expr = self._leaf.render_expr(payload.value)
        self.ctx.temps.flush(out, indent)
        resume = _StateLabel(_StateKind.RESUME, t.suspension_index).cpp_name()
        # The loop form resumes at the loop head whatever the state says.
        if self._loop_form is None:
            out.write(f"{indent}__state = {resume};\n")
        else:
            assert resume in self._loop_form.head_labels, resume
        out.write(f"{indent}return {yield_expr};\n")

    def _emit_suspend(self, out: "TextIO", indent: str,
                      payload: 'THIRAwaitSuspension',
                      suspension_index: int,
                      func: TpyFunction) -> None:
        """Emit the emplace + state-advance step at a Yield terminator.

        Inline mode: emplace the sub-coro struct directly via its ctor.
        Erased mode: move the operand value into the optional field.
        Borrowed mode: store the operand's address in the pointer field.
        """
        sub = self._sub_slot_name(payload, suspension_index)
        if payload.prebuilt_slot is not None:
            # Bound-coroutine await: the handle's frame slot is already
            # engaged (emplaced at the binding); nothing to construct.
            pass
        elif payload.mode is AwaitMode.INLINE:
            if payload.async_with_kind is not None:
                # Async-with synthetic yield. Receiver is the CM frame
                # slot; args differ by kind.
                ctx_n = payload.async_with_ctx_n
                recv = f"(*__with_ctx_{ctx_n})"
                if payload.async_with_kind is AsyncWithKind.AENTER:
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
                    args = self._emplace_args(payload)
                    self.ctx.temps.flush(out, indent)
                    out.write(f"{indent}{sub}.emplace({', '.join(args)});\n")
                elif isinstance(call, TpyMethodCall):
                    is_module_call = (call.user_module_call is not None
                                      or call.builtin_module_call is not None)
                    if is_module_call:
                        # `module.func(...)` -- receiver is a namespace,
                        # not a value, so the sub-coro ctor takes only
                        # the function args.
                        args = self._emplace_args(payload)
                        self.ctx.temps.flush(out, indent)
                        out.write(f"{indent}{sub}.emplace({', '.join(args)});\n")
                    else:
                        # Bound async method: prepend receiver as __self ctor arg.
                        recv_cpp = self._suspend_expr_cpp(payload.receiver)
                        arg_cpps = self._emplace_args(payload)
                        self.ctx.temps.flush(out, indent)
                        joined = ", ".join([recv_cpp] + arg_cpps)
                        out.write(f"{indent}{sub}.emplace({joined});\n")
                else:
                    raise CodeGenError(
                        "internal: inline-mode await operand is not a call",
                        loc=None)
        elif payload.mode is AwaitMode.ERASED:
            operand_cpp = self._suspend_expr_cpp(payload.operand)
            self.ctx.temps.flush(out, indent)
            out.write(f"{indent}{sub}.emplace(std::move({operand_cpp}));\n")
        elif payload.mode is AwaitMode.BORROWED:
            operand_cpp = self._suspend_expr_cpp(payload.operand)
            self.ctx.temps.flush(out, indent)
            published = self._leaf.published
            if payload.operand in published.owned_erased_operands:
                # Owned-erased handle (unique_ptr<P> local/param): the
                # sub-future points at the heap payload; polls dispatch
                # through the protocol vtable.
                out.write(f"{indent}{sub} = {operand_cpp}.get();\n")
            # A global / pointer-alias loop var already renders as `T*`;
            # re-taking its address would double-pointer the sub-future field.
            elif payload.operand in published.pointer_sources:
                out.write(f"{indent}{sub} = {operand_cpp};\n")
            else:
                out.write(f"{indent}{sub} = &({operand_cpp});\n")
        else:
            raise CodeGenError(f"unknown await mode {payload.mode!r}",
                               loc=None)
        out.write(f"{indent}__state = "
                  f"{_StateLabel(_StateKind.RESUME, suspension_index).cpp_name()};\n")
