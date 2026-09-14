"""Code generation for generator functions (yield -> state machine structs)."""
from __future__ import annotations


from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..parse.nodes import (
    TpyFunction, TpyStmt, TpyForEach, TpyCall, TpyCoerce, TpyMethodCall, TpyName, TpyExpr, TpyTupleUnpack,
)
from ..typesys import IntLiteralType, NominalType, OptionalType, ReadonlyType, TypeParamRef, TupleType, is_protocol_type, unwrap_own, unwrap_readonly, unwrap_ref_type, yield_borrow_slot_cpp, yield_uses_borrow_slot
from tpyc import modules as builtin_modules
from ..compilation_context import get_current_compiler
from ..symbol_binding import SymbolKind, lookup_imported
from ..type_def_registry import (iter_yields_ref_tuple_proxies,
                                  is_owned_in_coro_frame)
from . import emit_prims
from .context import CodeGenError
from .resumable_cfg import (ResumableShape, _stmts_have_any_suspension,
                            frame_struct_qualname, recursive_delegation_error,
                            same_module_dep_unit)
from .protocols import protocol_param_template_name


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
    # (loop var name, fully-spelled C++ payload for its frame field). The
    # payload is derived from the ITERATION SOURCE rather than the TPy element
    # type -- e.g. `::tpy::for_elem_next_t<T_items>` -- so C++ decides at
    # instantiation whether the element aliases the source or is owned, with
    # `frame_slot` supplying both forms behind one spelling. Name and payload
    # travel together so they cannot drift.
    #
    # Set ONLY by `iter_next`, and only where the choice is genuinely open:
    # that strategy's source may lend an element or hand back a fresh one, and
    # a protocol-typed or generic source settles which at instantiation. The
    # other strategies deliberately keep `pointer_form_loop_var` -- `begin_end`
    # and `next` can decide from the element type because their sources always
    # lend an lvalue, `range` synthesizes its element, and a concrete value
    # element is copy-only whatever the source. Widening this to them would
    # trade a legible `Box* b` for a trait sandwich and buy nothing.
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
    def _iter_slot_for_yield(elem_type: 'TpyType', cpp_elem: str) -> str:
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
        """
        unwrapped = unwrap_ref_type(unwrap_readonly(elem_type))
        if isinstance(unwrapped, TupleType):
            return elem_type.to_cpp_return()
        # A bare reference element is handed out by reference via val_or_ref<T>
        # (`val_or_ref<const T>` for a readonly one -- the const rides inside
        # the slot, so the pull still borrows instead of copying).
        # yield_uses_borrow_slot excludes the forms with their own representation
        # (Optional/Union pointer-or-storage, Own move, TypeParamRef already
        # val_or_ref-substituted by the caller).
        if yield_uses_borrow_slot(elem_type):
            return yield_borrow_slot_cpp(elem_type, cpp_elem)
        return cpp_elem

    def _analyze_for_strategy(self, stmt: TpyForEach, uid: int,
                              proto_param_names: frozenset[str] = frozenset(),
                              proto_param_alias: dict[str, str] | None = None
                              ) -> GeneratorForInfo | None:
        """Determine the iteration strategy and struct fields for a for-loop with yield.

        `proto_param_names` is the set of static-protocol param names of the
        enclosing coro; iterating directly over one types the iterator frame
        field against its deduced template arg `T_<pname>` (see the universal
        strategy below) rather than the un-instantiable concept rendering.
        `proto_param_alias` maps a forwarded local (`xs = it`) to the param it
        aliases, so iterating the alias reuses the same `T_<pname>` deduction.
        """
        from tpyc.modules import get_error_return_next_element_type

        elem_type = stmt.elem_type
        if elem_type and isinstance(elem_type, IntLiteralType):
            elem_type = self.ctx.analyzer.ctx.default_int_type
        elem_cpp = self.types.type_to_cpp(elem_type) if elem_type else "int32_t"
        # An iterator yielding a pointer-repr tuple hands out the borrow form
        # (std::tuple<..., T*>); the `__for_r` result slot must match that
        # ABI, not the ref/value rendering of the element type.
        elem_bare = (unwrap_readonly(unwrap_ref_type(elem_type))
                     if elem_type else None)
        if (isinstance(elem_bare, TupleType)
                and elem_bare.has_pointer_repr_element()):
            elem_cpp = self.types.tuple_borrow_cpp(elem_bare)

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

        # A narrowed value-Optional iterable (`str|None`/`bytes|None`) is still
        # `std::optional<V>` in the frame -- dispatch on / build the iterator
        # field type from the contained `V` (the resumable for-src render derefs
        # `(*v)` to match). Shared with the sync for-loop's type handling.
        iterable_type = emit_prims.narrowed_value_optional_iter_type(
            self.ctx, stmt.iterable,
            self.types.get_resolved_type(stmt.iterable))

        # Iterator[T] protocol -- iterable already has __next__()
        if is_protocol_type(iterable_type) and iterable_type.qualified_name() == "typing.Iterator":
            fields: list[tuple[str, str]] = []
            if self.ctx.is_temporary_expr(stmt.iterable):
                # The source iterator must live in the frame: re-emitting
                # the expression per advance would restart it every pass.
                # With the source struct known, spell the result slot from
                # its actual __next__ (the elem-type formula renders a
                # borrow element as `T&`, ill-formed inside std::expected).
                src_cpp = self._temp_iterator_field_cpp(stmt)
                fields.append((f"__for_src_{uid}", src_cpp))
                result_type = f"::tpy::iter_next_t<{src_cpp}>"
            else:
                # No source struct to read the slot off, so a CONCRETE
                # reference element takes the producer's own slot spelling --
                # the bare elem-type formula renders it `T&`, which
                # std::expected cannot hold. The peel is off `Ref[T]`, which is
                # how a for-head element type arrives. Every other element
                # keeps the formula: a generic `T` already spells the
                # instantiation-resolved `val_or_ref_t<T>` through it.
                slot_elem = unwrap_ref_type(elem_type) if elem_type else None
                slot_cpp = (
                    yield_borrow_slot_cpp(slot_elem,
                                          self.types.type_to_cpp(slot_elem))
                    if slot_elem is not None
                    and yield_uses_borrow_slot(slot_elem) else elem_cpp)
                result_type = f"std::expected<{slot_cpp}, ::tpy::StopIteration>"
            fields.append((f"__for_r_{uid}", result_type))
            # Non-value elements alias the producer's live yield slot (T*),
            # mirroring begin_end's pointer-form loop var -- a frame_slot
            # copy would hide loop-var mutations from the source elements.
            pointer_form_var = (
                stmt.var
                if (not stmt.is_tuple_unpack
                    and elem_wants_borrow_form(elem_type))
                else None
            )
            return GeneratorForInfo(
                uid=uid, strategy="next", fields=fields,
                pointer_form_loop_var=pointer_form_var,
                pointer_form_is_const=isinstance(
                    unwrap_ref_type(elem_type) if elem_type else None,
                    ReadonlyType))

        # error_return __next__ types (user iterators)
        er_elem = get_error_return_next_element_type(
            iterable_type, registry=self.ctx.analyzer.registry)
        if er_elem is not None:
            iter_cpp = self.types.type_to_cpp(iterable_type)
            result_type = f"std::expected<{elem_cpp}, ::tpy::StopIteration>"
            fields = []
            if self.ctx.is_temporary_expr(stmt.iterable):
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
            # A const-rooted lvalue chain (self.field in a readonly method,
            # const param/local) or a const borrow-alias frame local renders
            # const, so begin() yields a const_iterator -- the slot type must
            # match. Over-approximation is safe (iterator converts to
            # const_iterator); the __for_src_ copy slot below stays non-const
            # (temporaries are never const-storage sources).
            src_is_const = (
                self.ctx.is_const_storage_source(stmt.iterable)
                or (isinstance(stmt.iterable, TpyName)
                    and stmt.iterable.name
                    in self.ctx.generator_const_pointer_alias_locals))
            iter_container_cpp = (f"const {container_cpp}" if src_is_const
                                  else container_cpp)
            iter_type = f"::tpy::begin_iter_t<{iter_container_cpp}>"
            fields = [
                (f"__for_it_{uid}", iter_type),
                (f"__for_end_{uid}", iter_type),
            ]
            # A temporary source needs a frame field; a stable lvalue (name,
            # field path, container subscript) is borrowed instead -- copying
            # it into the frame would hide loop-var mutations from the source
            # (CPython aliasing semantics).
            if self.ctx.is_temporary_expr(stmt.iterable):
                fields.insert(0, (f"__for_src_{uid}", container_cpp))
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
                # in the struct emit and the tuple-unpack emit. Only plain
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
                pointer_form_var = (stmt.var
                                    if aliased and not yields_proxy else None)
                pointer_form_targets = frozenset(aliased)
            else:
                pointer_form_var = (
                    stmt.var
                    if (not yields_proxy
                        and (elem_wants_borrow_form(native_elem)
                             or whole_tuple_loop_var_aliases(native_elem)))
                    else None
                )
            borrow_tuple_var = (
                stmt.var
                if (yields_proxy and isinstance(elem_for_form, TupleType)
                    and elem_for_form.has_pointer_repr_element())
                else None)
            return GeneratorForInfo(
                uid=uid, strategy="begin_end", fields=fields,
                pointer_form_loop_var=pointer_form_var,
                pointer_form_is_const=(src_is_const
                                       or isinstance(elem_for_form,
                                                     ReadonlyType)),
                pointer_form_unpack_targets=pointer_form_targets,
                borrow_tuple_loop_var=borrow_tuple_var,
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
        subj_pname = None
        if isinstance(stmt.iterable, TpyName):
            subj_pname = (proto_param_alias or {}).get(
                stmt.iterable.name, stmt.iterable.name)
        if (subj_pname is not None
                and subj_pname in proto_param_names
                and self.functions.protocols.is_static_protocol_param(iterable_type)):
            src_cpp = protocol_param_template_name(subj_pname)
        else:
            src_cpp = self.types.type_to_cpp(iterable_type)
        iter_field_type = f"::tpy::iter_type_t<{src_cpp}>"
        result_field_type = f"::tpy::iter_result_t<{src_cpp}>"
        fields = [
            (f"__for_itr_{uid}", iter_field_type),
            (f"__for_r_{uid}", result_field_type),
        ]
        if self.ctx.is_temporary_expr(stmt.iterable):
            # `tpy::__iter__` borrows its argument, so a temporary source
            # must be stored in the frame first or the iterator dangles.
            if self.functions.protocols.is_static_protocol_param(iterable_type):
                raise CodeGenError(
                    "iterating a temporary protocol-typed iterable across a "
                    "suspension is not supported; bind the elements first "
                    "(e.g. `xs = list(...)`) and iterate those",
                    loc=stmt.loc)
            fields.insert(0, (f"__for_src_{uid}", src_cpp))
        # The source's `__next__` may lend an element or hand back a fresh one,
        # and for a protocol-typed or generic source that is settled only at
        # instantiation -- so the field's payload comes from the trait, not from
        # the TPy element type. A CONCRETE value element opts out: copy is the
        # only correct storage for it, so it keeps the plain field its sibling
        # value locals use rather than paying a slot for a choice that is not
        # open. A tuple-unpack loop keeps its own target classification, which
        # is a separate axis.
        loop_var_field = (
            None if (stmt.is_tuple_unpack or elem_is_known_value(elem_type))
            else (stmt.var, f"::tpy::for_elem_next_t<{src_cpp}>"))
        return GeneratorForInfo(uid=uid, strategy="iter_next", fields=fields,
                                loop_var_field=loop_var_field,
                                dep_units=self._iter_source_dep_units(
                                    iterable_type))

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

    def _temp_iterator_field_cpp(self, stmt: TpyForEach) -> str:
        """C++ frame-field type for a temporary `typing.Iterator` source:
        the callee generator's frame-struct name, qualified by
        `rcfg.frame_struct_qualname` so a callee in another module is
        spelled through its own namespace (its header is included here, and
        its struct is complete at the field declaration).

        Two shapes still reject, each because the field would be
        ill-formed rather than merely unhandled: a callee in a module that
        imports this one back (mutually infinite-size); and a source that
        is not a resolvable direct generator call at all.
        """
        resolved = self._resolve_generator_call(stmt.iterable)
        if resolved is None:
            raise CodeGenError(
                "a for-loop with a yield/await in its body over an "
                "Iterator-returning expression is only supported for a "
                "direct call to a generator; bind the elements first "
                "(e.g. `xs = list(...)`) and iterate those",
                loc=stmt.loc)
        callee = resolved.func
        call_node = resolved.call
        if self.functions.protocols.get_all_protocol_params(callee.params):
            raise CodeGenError(
                f"cannot iterate '{callee.name}(...)' here: a generator "
                "with protocol-typed parameters cannot be embedded in a "
                "resumable frame yet; bind the elements first "
                "(e.g. `xs = list(...)`) and iterate those",
                loc=stmt.loc)
        # `cycle_peers` includes this module itself, so the membership test
        # only means "mutually infinite-size" for a callee defined elsewhere.
        if (resolved.defining_module != self.ctx.analyzer.ctx.module_name
                and resolved.defining_module in self.ctx.cycle_peers):
            raise recursive_delegation_error(callee.name, loc=stmt.loc)
        inferred: tuple | None = None
        if callee.type_params:
            args = getattr(call_node, "inferred_type_args", None)
            if not args or len(args) != len(callee.type_params):
                raise CodeGenError(
                    f"cannot iterate '{callee.name}(...)' here: the generic "
                    "generator's type arguments were not resolved at the "
                    "call site", loc=stmt.loc)
            inferred = tuple(args)
        return frame_struct_qualname(
            self.types, resolved.owner, callee.name, inferred,
            module_qual=resolved.module_qual,
            shape=ResumableShape.GENERATOR, loc=stmt.loc)

    def _for_src_generator_targets(
            self, func: TpyFunction) -> list[tuple[str, str | None]]:
        """(name, owner_record) of same-module generator callees whose struct
        `func`'s resumable frame embeds by value via a `__for_src` field --
        emit-ordering dependencies (the embedded struct must be complete
        first)."""
        targets: list[tuple[str, str | None]] = []

        def walk(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                if (isinstance(s, TpyForEach) and not s.is_async
                        and (_stmts_have_any_suspension(s.body)
                             or _stmts_have_any_suspension(s.orelse))):
                    t = self.types.get_resolved_type(s.iterable)
                    if (t is not None and is_protocol_type(t)
                            and t.qualified_name() == "typing.Iterator"
                            and self.ctx.is_temporary_expr(s.iterable)):
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

        walk(func.body)
        return targets
