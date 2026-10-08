"""Code generation for generator functions (yield -> state machine structs)."""
from __future__ import annotations


from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..parse.nodes import (
    TpyFieldAccess, TpyFunction, TpyGeneratorExpression, TpyStmt, TpyForEach, TpyCall, TpyCoerce, TpyMethodCall, TpyName, TpyExpr, TpySubscript, TpyTupleUnpack,
)
from ..typesys import pointer_repr_optional
from ..typesys import IntLiteralType, NominalType, OptionalType, ReadonlyType, TypeParamRef, TupleType, is_protocol_type, unwrap_own, unwrap_readonly, unwrap_ref_type, yield_borrow_slot_cpp, yield_slot_borrows
from tpyc import modules as builtin_modules
from ..compilation_context import get_current_compiler
from ..symbol_binding import SymbolKind, lookup_imported
from ..type_def_registry import (iter_yields_ref_tuple_proxies,
                                  is_owned_in_coro_frame)
from . import emit_prims

from .resumable_cfg import (recursive_delegation_error, resumable_state,
                            same_module_dep_unit)
from ..value_category import (call_returns_cpp_ref, for_source_is_rvalue,
                              peel_coerce, property_access_returns_cpp_ref)


@dataclass(frozen=True)
class _DelegatedGenerator:
    """A resolved `for x in gen(...)` whose callee frame the enclosing
    resumable frame embeds by value in a `__for_src` field."""
    func: TpyFunction
    # Receiver type for a method callee; None for a free function. Carries
    # both the callee's namespace and its owner type args, so a generic
    # owner needs nothing extra.
    owner: 'NominalType | None'
    # Namespace qualifier for a cross-module FREE function. None for a
    # method (the owner supplies it) and for a same-module callee.
    module_qual: str | None
    # Where the callee is defined -- the axis the residual rejects key on,
    # not a spelling input.
    defining_module: str
    call: TpyExpr


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
    # Whether that pointer-form loop var is spelled `const T*`. The advance
    # takes `&(*it++)`, so the pointer's const-ness is the ITERATION SOURCE's:
    # a const-rooted source (readonly container) or a `readonly[E]` element
    # (what sema's `varargs_as_const` flip makes the default for `*args`)
    # both yield a `const T*`, and a plain `T*` field would not compile.
    pointer_form_is_const: bool = False
    # The `using` alias the struct defines for this loop's source type
    # (`__for_src_<uid>_t`); every field above is spelled through it. None
    # for the `range` counters, which have no source object.
    src_alias: str | None = None
    # Pointee spelling per pointer-form field of this loop -- the loop var
    # and the tuple-unpack targets that alias a member -- derived from the
    # SOURCE through `src_alias`, so the pointer's const is the element's.
    pointer_payloads: dict[str, str] = field(default_factory=dict)
    # (loop var name, fully-spelled C++ payload for its frame field):
    # `::tpy::for_elem_next_t<src_alias>`, so C++ decides at instantiation
    # whether the element aliases the source or is owned, with `frame_slot`
    # supplying both forms behind one spelling. Set ONLY by `iter_next`, the
    # one strategy where the choice is open: its source may lend an element
    # or hand back a fresh one. `begin_end` and `next` sources always lend an
    # lvalue, so their loop var is the pointer form above; `range`
    # synthesizes its element; a concrete value element is copy-only.
    loop_var_field: tuple[str, str] | None = None
    # For a tuple-unpack loop (`for a, b in items:`) whose element tuple
    # contains non-value, non-readonly members, the names of the unpack
    # targets bound to those members. They are stored as `T*` (alias into
    # the live container element via the pointer-form `__for_tup`), so
    # mutating an unpacked record propagates to the source -- matching both
    # CPython and the plain pointer-form loop var above. Value/readonly
    # members are NOT listed (they stay value-copy). Empty for non-unpack
    # loops and all-value tuples.
    pointer_form_unpack_targets: frozenset[str] = frozenset()
    # Loop variable name when the element is a pointer-repr `Optional[T]` over
    # a container that lends lvalues (`begin_end`): the frame field is the
    # nullable `T*`, bound at the advance by `optional_to_ptr` of the slot.
    opt_ptr_loop_var: str | None = None
    # Loop variable name when the iterator yields PROXY reference tuples
    # (dict_items: operator* returns std::tuple<const K&, V&> by value).
    # The loop element cannot be address-taken (`&(*it)` is ill-formed on
    # the prvalue proxy), so the loop var's frame slot is the borrow-form
    # tuple (std::tuple<..., T*>) assigned via tuple_to_pointer; the element
    # refs point into stable node storage, so aliasing across yield/resume
    # still holds. None for non-proxy iterators.
    borrow_tuple_loop_var: str | None = None
    # `(name, owner_record)` of the same-module generator method backing this
    # loop's iteration source, when the frame embeds its struct by value.
    # `iter_next` spells its iterator / result / loop-var fields as
    # `iter_type_t<S>` & friends, which need `S::__iter__`'s RETURN TYPE
    # complete -- another frame struct when that `__iter__` is itself a
    # generator. Recorded here because this is where the source
    # type is resolved; consumed as an emit-ordering edge.
    dep_units: tuple[tuple[str, str | None], ...] = ()
    # Sema's verdict on whether the ITERATION SOURCE is const in this frame (a
    # const-borrow capture, a const-bound local, or a chain rooted at one).
    # The loop's own fields take their const from the source's C++ type; this
    # is for the loop var's BINDING, which the rest of the body reads.
    source_is_const: bool = False


def owned_view_frame_params(
        params: 'list[tuple[str, TpyType]]') -> 'set[str]':
    """Param names whose view is copied into OWNED storage entering a
    generator/coroutine body.

    The resumable frame (`_CoroParamKind.OWNED_COPY`) keys the copy on
    `is_owned_in_coro_frame`; one derivation, so the frame layout and the
    body cannot drift on which params a body reads as owned.
    """
    return {pname for pname, ptype in params if is_owned_in_coro_frame(ptype)}


def elem_wants_borrow_form(elem_type: 'TpyType | None') -> bool:
    """Whether a frame-resident loop var over elements of this type must be a
    borrow-form `T*` aliasing the source rather than an owning `frame_slot<T>`.
    The owning copy hides loop-var mutations from the source elements -- a
    silent divergence from CPython's aliasing.

    For the `begin_end` and `next` strategies only, and deliberately so: their
    sources always lend a genuine lvalue -- a container element, a producer's
    live yield slot -- so the answer is decidable from the element type alone
    whatever it turns out to be, GENERICS INCLUDED (`*it` on a `vector<T>` is an
    lvalue for every `T`). `iter_next` cannot use this and does not: an
    arbitrary `__next__` may hand back a fresh value that looks identical to a
    lent one here, so its field defers to `for_elem_next_t` and lets C++ decide.

    Value elements copy by their own semantics. A `readonly[T]` element rides
    too: it is the DEFAULT element of a non-mutated `*args` pack (sema's
    `varargs_as_const` flip), so excluding it would make the silent copy the
    normal case rather than a corner. Its pointer is spelled `const T*`: the
    two `GeneratorForInfo` construction sites below decide that with
    `pointer_form_is_const` -- from the iteration source's const verdict or a
    `readonly` element -- and the frame layout consumes it through the same
    const-alias set the statement-level const aliases use.
    """
    elem = unwrap_ref_type(elem_type) if elem_type is not None else None
    return (elem is not None
            and not unwrap_readonly(elem).is_value_type())


def whole_tuple_loop_var_aliases(elem_type: 'TpyType | None') -> bool:
    """Whether a NON-unpack tuple loop var is a POINTER to the source element
    tuple (`std::tuple<..., T>*`) rather than a field of its own.

    A tuple with a reference element has two C++ shapes, and a frame field in
    either one disagrees with the advance: the storage tuple `*it` yields
    cannot assign into a borrow-form field, and a borrow-form field rebuilt
    per advance would still have to name the storage the pointers point at.
    Pointing at the source element -- exactly what the tuple-UNPACK sibling's
    `__for_tup` holder does -- has one shape, needs no runtime helper, and
    aliases the source like CPython's `auto&&` sync twin.

    An ALL-VALUE element tuple takes the same field rather than the value copy
    it could get away with: one form answers the question for both element
    shapes, so there is no second classification to keep in step.

    For `begin_end` sources only -- the ones that lend a genuine lvalue.
    An `__iter__`/`__next__` source hands the step result back BY VALUE for a
    tuple element (`val_or_ref_t` treats a tuple as a value type), so there
    is no source element to point at; its own defect is that the sync emitter
    reads such an element in borrow form regardless (see BUGS.md).
    """
    elem = (unwrap_readonly(unwrap_ref_type(elem_type))
            if elem_type is not None else None)
    return isinstance(elem, TupleType)


def elem_is_known_value(elem_type: 'TpyType | None') -> bool:
    """Whether the element is a CONCRETE value type, so a plain frame field is
    right and the storage-form trait is not needed.

    For a scalar this is decidable and cannot be silently wrong, unlike
    borrow-vs-own for a reference element: copying is the whole semantics, so
    there is no aliasing outcome to lose. A generic or protocol-typed element is
    NOT known -- it instantiates either way -- and `readonly[T]` defers too, so
    the trait can settle its constness.

    A TUPLE element also lands here, but for a weaker reason: `is_value_type()`
    is unconditionally True for TupleType even when the tuple holds borrows, so
    "copying is correct" does NOT follow. Returning True is still right, because
    a tuple loop var has its own established classification (borrow-form tuple
    fields, tuple-unpack alias targets) and must reach it rather than be
    rerouted through this trait.
    """
    elem = unwrap_ref_type(elem_type) if elem_type is not None else None
    if elem is None or isinstance(elem, (TypeParamRef, ReadonlyType)):
        return False
    if is_protocol_type(elem):
        return False
    return elem.is_value_type()


if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeMapper
    from .functions import FunctionGenerator


class GeneratorCodegen:
    """Generates C++ code for generator functions as state machine structs."""

    def __init__(
        self,
        ctx: CodeGenContext,
        types: TypeMapper,
        functions: FunctionGenerator,
    ):
        self.ctx = ctx
        self.types = types
        self.functions = functions
        # Generators defined in the module being emitted, keyed
        # (name, owner_record). Filled by the per-module pre-scan before any
        # emit pass, so a same-module callee is looked up here rather than
        # re-resolved through the binding table.
        self.same_module_generators: dict[
            tuple[str, str | None], TpyFunction] = {}

    @staticmethod
    def _iter_slot_for_yield(elem_type: 'TpyType', cpp_elem: str,
                             generic_borrows: bool) -> str:
        """Pick the iterator slot type for a generator yield.

        Iterator yields hand out references like function returns (CPython
        semantics), so borrow form is the default for tuple yields:
        `tuple[int32, Point]` -> `std::tuple<int32_t, Point&>`,
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

        `generic_borrows` is the producer's provenance verdict
        (`TpyFunction.generic_yield_borrows`) and answers the one case the
        element type alone cannot: an OPEN `T` spells the instantiation-
        resolved `::tpy::yield_slot_t<T>` only when this generator's yield
        sources all outlive a suspension; otherwise it keeps the value slot.
        """
        if isinstance(unwrap_ref_type(unwrap_readonly(elem_type)), TupleType):
            return elem_type.to_cpp_return()
        # A bare reference element is handed out by reference via val_or_ref<T>
        # (`val_or_ref<const T>` for a readonly one -- the const rides inside
        # the slot, so the pull still borrows instead of copying).
        # yield_slot_borrows excludes the forms with their own representation
        # (Optional/Union pointer-or-storage, Own move) and folds in the
        # open-`T` verdict.
        if yield_slot_borrows(elem_type, generic_borrows):
            return yield_borrow_slot_cpp(elem_type, cpp_elem)
        return cpp_elem

    def _analyze_for_strategy(self, stmt: TpyForEach, uid: int,
                              pinned_roots: frozenset[str] = frozenset(),
                              deduced_source_concrete: bool = False
                              ) -> GeneratorForInfo | None:
        """Determine the iteration strategy and struct fields for a for-loop
        with yield.

        Every field of the loop is spelled off ONE alias, `__for_src_<uid>_t`,
        which the struct emit defines as `decltype((<source render>))` -- the
        source expression's own C++ type, reference and const included (an
        rvalue's is stripped to the value the frame owns). A `T&`-returning
        method on a const receiver, a view of a readonly dict, a conditional
        of two const params, a lazy combinator, a generator expression: each
        spells its iterator and result slots through that alias without this
        analysis naming the type, so it cannot disagree with what the setup
        actually stores. What IS decided here: the strategy, which fields
        exist, whether the frame holds the source, and the loop var's form.

        `pinned_roots` are the names (params, captures, `self`) whose storage
        outlives the frame. An lvalue ITERATOR source rooted at one of them
        is held by reference in the frame so it is evaluated once.
        `deduced_source_concrete` says a protocol-typed source is a genexpr's
        deduced slot, instantiated by its one creation site -- not an open
        protocol param that may be driven at a container of value tuples.
        """
        from tpyc.modules import get_error_return_next_element_type

        elem_type = stmt.elem_type
        if elem_type and isinstance(elem_type, IntLiteralType):
            elem_type = self.ctx.analyzer.ctx.default_int_type
        alias = f"__for_src_{uid}_t"
        # Sema's answer for "is this source const here", recorded for the loop
        # var's BINDING (the frame's const bindings, which the rest of the body
        # reads); the loop's own fields carry their const through the alias.
        src_is_const = self.ctx.is_const_storage_source(stmt.iterable)
        holds_source = for_source_is_rvalue(stmt, self.ctx.analyzer)

        # Range counter optimization
        if isinstance(stmt.iterable, TpyCall) and stmt.iterable.func_name == "range":
            elem_cpp = self.types.type_to_cpp(elem_type) if elem_type else "int32_t"
            fields: list[tuple[str, str]] = [
                (f"__for_i_{uid}", elem_cpp),
                (f"__for_stop_{uid}", elem_cpp),
            ]
            nargs = len(stmt.iterable.args)
            if nargs == 3:
                fields.append((f"__for_step_{uid}", elem_cpp))
            return GeneratorForInfo(uid=uid, strategy="range", fields=fields)

        # A narrowed value-Optional iterable (`str|None`/`bytes|None`) is still
        # `std::optional<V>` in the frame -- dispatch on the contained `V` (the
        # source render derefs `(*v)` to match). Shared with the sync for-loop.
        iterable_type = emit_prims.narrowed_value_optional_iter_type(
            self.ctx, stmt.iterable,
            self.types.get_resolved_type(stmt.iterable))

        # The source IS the iterator: `typing.Iterator` (a generator frame, a
        # combinator, a protocol param) or a user type whose `__next__` is
        # `@error_return`.
        is_iterator = (is_protocol_type(iterable_type)
                       and iterable_type.qualified_name() == "typing.Iterator")
        if is_iterator or get_error_return_next_element_type(
                iterable_type, registry=self.ctx.analyzer.registry) is not None:
            fields: list[tuple[str, str]] = []
            if holds_source or self._lvalue_source_pinned(stmt, pinned_roots):
                # The source iterator lives in the frame -- owned when fresh,
                # by reference when it is existing storage reached through a
                # call: re-rendering the call per advance would run it once
                # per element. The frame struct it embeds must be complete.
                self._check_delegated_source(stmt)
                fields.append((f"__for_src_{uid}", alias))
                holds_source = True
            fields.append((f"__for_r_{uid}", f"::tpy::iter_next_t<{alias}>"))
            step_elem = f"::tpy::step_elem_t<::tpy::iter_next_t<{alias}>>"
            # Non-value elements alias the producer's live yield slot (T*),
            # mirroring begin_end's pointer-form loop var -- a frame_slot
            # copy would hide loop-var mutations from the source elements.
            # An unpack head aliases its reference members the same way.
            pointer_payloads = self._step_unpack_payloads(stmt, elem_type,
                                                          step_elem)
            pointer_form_var = (
                stmt.var
                if (not stmt.is_tuple_unpack
                    and elem_wants_borrow_form(elem_type))
                else None
            )
            if pointer_form_var is not None:
                pointer_payloads[pointer_form_var] = step_elem
            elif pointer_payloads:
                pointer_form_var = stmt.var
            return GeneratorForInfo(
                uid=uid, strategy="next", fields=fields, src_alias=alias,
                source_is_const=src_is_const,
                pointer_form_loop_var=pointer_form_var,
                pointer_form_is_const=isinstance(
                    unwrap_ref_type(elem_type) if elem_type else None,
                    ReadonlyType),
                pointer_form_unpack_targets=frozenset(
                    n for n in pointer_payloads if n != stmt.var),
                pointer_payloads=pointer_payloads)

        # Built-in NativeIterable containers (list, dict, set, Array, Span, str) -- begin/end
        record = self.ctx.analyzer.registry.get_record_for_type(iterable_type)
        is_builtin_ni = (record is not None and record.is_native
                         and builtin_modules.is_native_iterable(iterable_type, registry=self.ctx.analyzer.registry))
        native_elem = builtin_modules.get_iterable_element_type(iterable_type, registry=self.ctx.analyzer.registry) if is_builtin_ni else None
        if native_elem is not None:
            iter_type = f"::tpy::begin_iter_t<{alias}>"
            fields = [
                (f"__for_it_{uid}", iter_type),
                (f"__for_end_{uid}", iter_type),
            ]
            # A fresh source needs a frame field; existing storage (a name, a
            # field path, a reference-returning call) is borrowed instead --
            # copying it into the frame would hide loop-var mutations from the
            # source (CPython aliasing semantics).
            if holds_source:
                fields.insert(0, (f"__for_src_{uid}", alias))
            # Non-value element type with stable lvalue source: the loop var
            # becomes T* (aliasing the container element) instead of
            # std::optional<T> (value-copy). Preserves CPython aliasing
            # semantics across yield/resume.
            # `&(*iter)` is a `const T*` whenever the source iterates const --
            # a const-rooted container or a `readonly[E]` element -- so the
            # slot takes the const spelling rather than falling back to a
            # value copy.
            elem_for_form = unwrap_ref_type(native_elem) if native_elem else None
            # Proxy-ref tuple iterators (dict_items): `&(*it)` is ill-formed
            # on the prvalue proxy, so the loop element binds as a borrow-form
            # tuple via tuple_to_pointer instead of a `T*` to the element.
            yields_proxy = iter_yields_ref_tuple_proxies(iterable_type)
            pointer_form_targets: frozenset[str] = frozenset()
            target_payloads: dict[str, str] = {}
            opt_ptr_var: str | None = None
            if (stmt.is_tuple_unpack and isinstance(elem_for_form, TupleType)
                    and stmt.body and isinstance(stmt.body[0], TpyTupleUnpack)):
                # Tuple-unpack over a stable lvalue container: alias the
                # reference members so mutating an unpacked record propagates
                # to the source element (CPython semantics, matching the plain
                # pointer-form loop var). `__for_tup` becomes T* and the
                # aliased targets become T* too via `&std::get<i>(...)`; a
                # holder with nothing to alias stays a value copy.
                aliased = self._unpack_alias_members(stmt, elem_for_form)
                pointer_form_var = (stmt.var
                                    if aliased and not yields_proxy else None)
                pointer_form_targets = frozenset(aliased.values())
                if pointer_form_var is not None:
                    target_payloads = {
                        tname: f"std::tuple_element_t<{i}, "
                               f"::tpy::begin_elem_t<{alias}>>"
                        for i, tname in aliased.items()}
            else:
                # A pointer-repr `Optional[T]` element is NOT a `T*` to the
                # container's slot: its loop var is the nullable `T*` every
                # other position spells (`optional_to_ptr` at the advance), so
                # a `None` test on it asks the element, not the slot's address.
                opt_ptr_var = (
                    stmt.var
                    if (not yields_proxy
                        and pointer_repr_optional(elem_for_form) is not None)
                    else None)
                pointer_form_var = (
                    stmt.var
                    if (not yields_proxy and opt_ptr_var is None
                        and (elem_wants_borrow_form(native_elem)
                             or whole_tuple_loop_var_aliases(native_elem)))
                    else None
                )
            borrow_tuple_var = (
                stmt.var
                if (yields_proxy and isinstance(elem_for_form, TupleType)
                    and elem_for_form.has_pointer_repr_element())
                else None)
            pointer_payloads = dict(target_payloads)
            if pointer_form_var is not None:
                pointer_payloads[pointer_form_var] = (
                    f"::tpy::begin_elem_t<{alias}>")
            return GeneratorForInfo(
                uid=uid, strategy="begin_end", fields=fields, src_alias=alias,
                source_is_const=src_is_const,
                pointer_form_loop_var=pointer_form_var,
                pointer_form_is_const=(src_is_const
                                       or isinstance(elem_for_form,
                                                     ReadonlyType)),
                pointer_form_unpack_targets=pointer_form_targets,
                pointer_payloads=pointer_payloads,
                borrow_tuple_loop_var=borrow_tuple_var,
                opt_ptr_loop_var=opt_ptr_var,
            )

        # Universal default: ::tpy::__iter__() + __next__() loop.
        # Handles Iterable[T]/NativeIterable[T] protocol params, user types
        # with __iter__(), and any remaining iterable types.
        fields = [
            (f"__for_itr_{uid}", f"::tpy::iter_type_t<{alias}>"),
            (f"__for_r_{uid}", f"::tpy::iter_result_t<{alias}>"),
        ]
        if holds_source or self._lvalue_source_pinned(stmt, pinned_roots):
            # `tpy::__iter__` borrows its argument, so a fresh source is stored
            # in the frame first or the iterator dangles; existing storage
            # reached through a call is held by reference, so the call runs
            # once rather than at every advance of a self-iterator.
            fields.insert(0, (f"__for_src_{uid}", alias))
            holds_source = True
        # The source's `__next__` may lend an element or hand back a fresh one,
        # and for a protocol-typed or generic source that is settled only at
        # instantiation -- so the field's payload comes from the trait, not from
        # the TPy element type. A CONCRETE value element opts out: copy is the
        # only correct storage for it, so it keeps the plain field its sibling
        # value locals use rather than paying a slot for a choice that is not
        # open. A tuple-unpack loop keeps its own target classification, which
        # is a separate axis.
        #
        # A tuple-unpack head whose tuple has reference members points the
        # HOLDER at the step result in the frame's own result slot, and its
        # reference targets alias the members through it (`p =
        # &std::get<0>(*__for_tup)`). The result stays there until the next
        # advance, so the alias holds for the iteration, and nothing is copied
        # to take it -- a proxy tuple of references (`zip`, `enumerate`,
        # `dict.items()`) has no value form to copy into. Not for an OPEN
        # protocol-typed source: it may instantiate at a native container of
        # value tuples, whose adaptor hands back a COPY of the element, and a
        # mutation through the target would then miss the source silently.
        elem_bare = unwrap_ref_type(elem_type) if elem_type is not None else None
        source_open = (
            self.functions.protocols.is_static_protocol_param(iterable_type)
            and not deduced_source_concrete)
        pointer_payloads: dict[str, str] = (
            {} if source_open else self._step_unpack_payloads(
                stmt, elem_type, f"::tpy::for_step_elem_t<{alias}>"))
        aliased_targets = frozenset(n for n in pointer_payloads
                                    if n != stmt.var)
        # A pointer-repr `Optional[T]` element is the nullable `T*` here as
        # under `begin_end`: the step result stays in the frame's result slot
        # until the next advance, lent or fresh, so the pointer holds for the
        # iteration.
        elem_opt = unwrap_readonly(elem_bare) if elem_bare is not None else None
        opt_ptr_var = (
            stmt.var if (not stmt.is_tuple_unpack
                         and pointer_repr_optional(elem_opt) is not None)
            else None)
        loop_var_field = (
            None if (stmt.is_tuple_unpack or opt_ptr_var is not None
                     or elem_is_known_value(elem_type))
            else (stmt.var, f"::tpy::for_elem_next_t<{alias}>"))
        return GeneratorForInfo(uid=uid, strategy="iter_next", fields=fields,
                                src_alias=alias,
                                loop_var_field=loop_var_field,
                                opt_ptr_loop_var=opt_ptr_var,
                                pointer_form_is_const=(
                                    src_is_const
                                    or isinstance(elem_bare, ReadonlyType)),
                                pointer_form_loop_var=(
                                    stmt.var if aliased_targets else None),
                                pointer_payloads=pointer_payloads,
                                pointer_form_unpack_targets=aliased_targets,
                                source_is_const=src_is_const,
                                dep_units=self._iter_source_dep_units(
                                    iterable_type))

    @staticmethod
    def _unpack_alias_members(stmt: TpyForEach,
                              elem_bare: 'TpyType | None') -> dict[int, str]:
        """The members a tuple-unpack head ALIASES, by tuple index -> target
        name: the plain reference members. A value or `readonly` member is
        copied, and an Optional member takes its own pointer-repr path
        (`T* = nullptr`, `optional_to_ptr`). Empty for a non-unpack loop."""
        if not (stmt.is_tuple_unpack and isinstance(elem_bare, TupleType)
                and stmt.body and isinstance(stmt.body[0], TpyTupleUnpack)):
            return {}
        return {
            i: tname
            for i, (tname, etype) in enumerate(zip(
                stmt.body[0].targets, elem_bare.element_types))
            if tname is not None
            and not unwrap_ref_type(etype).is_value_type()
            and not isinstance(unwrap_ref_type(etype),
                               (ReadonlyType, OptionalType))}

    def _step_unpack_payloads(self, stmt: TpyForEach,
                              elem_type: 'TpyType | None',
                              step_elem: str) -> dict[str, str]:
        """Pointee spellings for a tuple-unpack head over an ITERATOR
        source: the holder points at the step result in the frame's own
        result slot (`step_elem`), and each aliased member's target points
        through it. The result stays there until the next advance, so the
        alias holds for the iteration, and nothing is copied to take it -- a
        proxy tuple of references (`zip`, `enumerate`, `dict.items()`) has
        no value form to copy into."""
        elem_bare = unwrap_ref_type(elem_type) if elem_type is not None else None
        payloads = {
            tname: f"::tpy::step_elem_member_t<{i}, {step_elem}>"
            for i, tname in self._unpack_alias_members(stmt, elem_bare).items()}
        if payloads:
            payloads[stmt.var] = step_elem
        return payloads

    def _lvalue_source_pinned(self, stmt: TpyForEach,
                              pinned_roots: frozenset[str]) -> bool:
        """An lvalue ITERATOR source the frame holds by reference: a method
        call (which may be impure, so it must run once) whose receiver chain
        is rooted at storage that outlives the frame. A chain rooted at a
        frame local of the frame's own is not held and keeps re-rendering the
        call (`BUGS.md#frame-iter-next-source-reevaluated`).
        """
        e = peel_coerce(stmt.iterable)
        if not isinstance(e, TpyMethodCall):
            return False
        # Every hop below the root must LEND: a method returning by value
        # mints a temporary the reference would dangle into. A getter's
        # convention is the property predicate's (a storage-ref return, an
        # open `T` settled at the instantiation).
        analyzer = self.ctx.analyzer
        root: TpyExpr = e
        while isinstance(root, (TpyMethodCall, TpyFieldAccess, TpySubscript)):
            if isinstance(root, TpyMethodCall) and not (
                    property_access_returns_cpp_ref(analyzer, root)
                    or call_returns_cpp_ref(analyzer,
                                            root.resolved_function_info)):
                return False
            root = root.obj
        return isinstance(root, TpyName) and root.name in pinned_roots

    def _iter_source_dep_units(
            self, iterable_type: 'TpyType | None',
    ) -> tuple[tuple[str, str | None], ...]:
        """The `("__iter__", record)` emit-ordering dep for an `iter_next`
        source, or empty when the source has no same-module generator
        `__iter__`.

        A protocol-typed source resolves to no record and yields nothing; its
        field renders against a deduced template arg, so completeness is
        settled at instantiation rather than at the field declaration.

        The same-module test is the OWNER'S module, not membership in
        `same_module_generators`: that map is keyed on the bare
        `(method, record)` pair, so a cross-module type whose name collides
        with a local record matches the local entry and fabricates a cycle.
        """
        inner = unwrap_readonly(unwrap_own(unwrap_ref_type(iterable_type)))
        dep = same_module_dep_unit(inner, "__iter__",
                                   self.ctx.analyzer.ctx.module_name)
        if dep is None or dep not in self.same_module_generators:
            return ()
        return (dep,)

    @staticmethod
    def _module_generator(module: str, name: str,
                          owner_record: str | None) -> TpyFunction | None:
        """The `TpyFunction` for a generator defined in `module`, or None
        when the name does not resolve there or is not a generator. Every
        module is fully analyzed before any of them emits, so a callee's
        AST is available whatever the emit order."""
        compiler = get_current_compiler()
        compiled = compiler.modules.get(module) if compiler else None
        if compiled is None:
            return None
        mod_ast = compiled.ast
        if owner_record is not None:
            for record in mod_ast.all_records():
                if record.name != owner_record:
                    continue
                for m in record.methods:
                    if m.name == name and m.is_generator:
                        return m
            return None
        for f in mod_ast.functions:
            if f.name == name and f.is_generator and not f.skip_codegen:
                return f
        return None

    def _resolve_generator_call(
            self, expr: TpyExpr,
    ) -> '_DelegatedGenerator | None':
        """Resolve a direct call to a generator whose frame a `__for_src`
        field can embed by value, wherever that generator is defined. None
        for any other shape (non-call expression, unknown callee, a call
        through a variable).

        The same-module map is consulted first because it is the pre-scan's
        own view of this module; anything else is resolved through the
        binding that named the callee, which is what makes the callee's
        defining module -- and so its C++ namespace -- known.
        """
        e = expr
        while isinstance(e, TpyCoerce):
            e = e.expr
        cur = self.ctx.analyzer.ctx.module_name
        if isinstance(e, TpyCall) and isinstance(e.func, TpyName):
            f = self.same_module_generators.get((e.func_name, None))
            if f is not None:
                return _DelegatedGenerator(f, None, None, cur, e)
            # `from m import g; ... for x in g():` -- the attribute table's
            # binding is chain-flattened and shadow-correct, and carries the
            # name the callee is defined under in its own module.
            qual = lookup_imported(self.ctx.analyzer.ctx.module_attributes,
                                   e.func_name, SymbolKind.FUNCTION)
            if qual is None or qual[0] == cur:
                return None
            f = self._module_generator(qual[0], qual[1], None)
            return (_DelegatedGenerator(f, None, qual[0], qual[0], e)
                    if f is not None else None)
        if isinstance(e, TpyMethodCall):
            mod = e.user_module_call or e.builtin_module_call
            if mod is not None:
                # `import m; ... for x in m.g():` -- a free function reached
                # through the module object, not a method.
                f = self._module_generator(mod, e.method, None)
                if f is None:
                    return None
                return _DelegatedGenerator(
                    f, None, None if mod == cur else mod, mod, e)
            recv = unwrap_ref_type(self.types.get_resolved_type(e.obj))
            owner_name = getattr(recv, "name", None)
            if owner_name is None:
                return None
            f = self.same_module_generators.get((e.method, owner_name))
            if f is not None:
                return _DelegatedGenerator(f, recv, None, cur, e)
            qname = recv.qualified_name() if isinstance(recv, NominalType) \
                else None
            if not qname or "." not in qname:
                return None
            owner_mod = qname.rsplit(".", 1)[0]
            if owner_mod == cur:
                return None
            f = self._module_generator(owner_mod, e.method, owner_name)
            return (_DelegatedGenerator(f, recv, None, owner_mod, e)
                    if f is not None else None)
        return None

    def _check_delegated_source(self, stmt: TpyForEach) -> None:
        """Reject a `typing.Iterator` source the frame cannot hold: a direct
        call to a generator defined in a module that imports this one back.
        Its frame would be embedded by value in this one and vice versa, so
        neither header can complete the other's struct (mutually
        infinite-size). Any other source needs no check here: the field's
        type is the source expression's own, deduced by C++."""
        resolved = self._resolve_generator_call(stmt.iterable)
        if resolved is None:
            return
        # `cycle_peers` includes this module itself, so the membership test
        # only means "mutually infinite-size" for a callee defined elsewhere.
        if (resolved.defining_module != self.ctx.analyzer.ctx.module_name
                and resolved.defining_module in self.ctx.cycle_peers):
            raise recursive_delegation_error(resolved.func.name, loc=stmt.loc)

    def _for_src_generator_targets(
            self, func: TpyFunction) -> list[tuple[str, str | None]]:
        """(name, owner_record) of same-module generator callees whose struct
        `func`'s resumable frame embeds by value via a `__for_src` field --
        emit-ordering dependencies (the embedded struct must be complete
        first)."""
        state = resumable_state(func)
        targets: list[tuple[str, str | None]] = []

        def frame_holds_source(s: TpyForEach) -> bool:
            info = state.for_loop_info.get(s)
            if info is not None:
                return any(n.startswith("__for_src_") for n, _ in info.fields)
            return state.for_src_fields.get(s) is not None

        def walk(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                # Whether the loop SUSPENDS does not enter it; whether the
                # frame ends up HOLDING the source does. A loop whose holder
                # stays in its block names no field, so it orders nothing.
                if (isinstance(s, TpyForEach) and not s.is_async
                        and frame_holds_source(s)):
                    src = peel_coerce(s.iterable)
                    if (isinstance(src, TpyGeneratorExpression)
                            and src.frame_func is not None):
                        # A genexpr's frame is a unit of this module too.
                        targets.append((src.frame_func.name, None))
                    t = self.types.get_resolved_type(s.iterable)
                    if (t is not None and is_protocol_type(t)
                            and t.qualified_name() == "typing.Iterator"):
                        r = self._resolve_generator_call(s.iterable)
                        # Same-module only: the keys are bare `(name, owner)`
                        # pairs, so a cross-module callee would match a
                        # same-named local unit and fabricate an edge -- and
                        # it needs no ordering (its header is complete).
                        if (r is not None and r.defining_module ==
                                self.ctx.analyzer.ctx.module_name):
                            owner = r.owner.name if r.owner is not None \
                                else None
                            targets.append((r.func.name, owner))
                for b in (s.sub_bodies() if hasattr(s, "sub_bodies") else ()):
                    walk(b)

        # The prescan's verdicts are keyed on the nodes of the await-lifted
        # body, so the walk must see those same nodes.
        walk(state.lifted_body or func.body)
        return targets
