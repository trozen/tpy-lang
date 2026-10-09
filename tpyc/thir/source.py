"""What a lowered expression IS, as one fact on its THIR node.

Every THIR expression `_lower_expr` returns carries a `Source`
(`THIRExpr.source`), written once at the tail of `_lower_expr`
(`expressions.stamp_source`). A sink reads it instead of re-deriving the
same answers from the node's kind: where the value lives, whether it is
const, whether a sink may move it, whether its C++ value is a raw pointer.
`tpyc/thir/lower/convert.py` decides every conversion from it and the
destination slot alone.

Plain data on purpose: the module imports no lowering code, so `nodes.py`
can name the type and every consumer can read it without a cycle.
"""
from __future__ import annotations

from collections.abc import Callable, Set as AbstractSet
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from ..typesys import TpyType

if TYPE_CHECKING:
    from .lower.context import ValueOptKind
    from .nodes import THIRTupleLayout


class Held(Enum):
    """Where the value lives, in the C++ representation it renders as,
    decided from what the expression is (`expressions._held_from_facts`):
    a name from its binding, a member or element from the slot it names, a
    call from its callee's declared result, an rvalue from its freshness.
    It maps onto `Form`: BORROWED is `Form.BORROW`, STORAGE and FRESH are
    `Form.STORAGE` split by whether the expression names existing storage,
    and VALUE is `Form.VALUE`, the representation in which a borrow and the
    storage coincide (a value type, or a record read as the object)."""
    # An lvalue in its storage representation: an owned local, a member or
    # element read naming its slot's own optional / variant / tuple.
    STORAGE = auto()
    # In the borrow representation: a `T*` Optional, a pointer variant, a
    # mixed own / borrow tuple, a view of someone else's buffer.
    BORROWED = auto()
    # An owned rvalue in storage representation: an `Own`-declared call
    # result, a literal built as storage.
    FRESH = auto()
    VALUE = auto()


@dataclass(frozen=True, slots=True)
class SelectResult:
    """What a select (`a if c else b`, a value `and` / `or`) yields after
    its lowering normalized the arms: the representation the `?:` renders
    in -- a view, an owned buffer naming an operand's storage, a borrowed
    lvalue, a fresh owned value, a plain value -- and whether that result
    dies with its full expression. Only the lowering that materializes or
    casts the arms knows it; the arms' own answers do not compose into it
    (a view arm and a literal arm make a view)."""
    held: Held
    temporary: bool


@dataclass(frozen=True, slots=True, eq=False)
class BindingRepr:
    """How one C++ binding holds its value, decided once per declaration
    site by the binding table (`lower/bindings.py`): the home of the
    per-name representation facts the lowering reads (`_LowerCtx.binding`),
    and the record a NAME read reads (`Source.binding`). Every THIR node
    that declares or reseats a binding carries its record as `binding`;
    the validator checks the node's facts against it."""
    name: str
    # The declaring statement, comprehension clause, walrus or match case;
    # None for a parameter, a seeded global or a frame field.
    site: object | None
    # The declared type (a flow-narrowed read has a narrower
    # `result_type`).
    type: TpyType | None
    # The classifier row that decided the record.
    row: str
    is_param: bool = False
    # The type the parameter list the body is lowered under (`lc.params`)
    # gives the name, `Ref[...]` and all: set for the function's own
    # parameters and, inside a lambda, the lambda's; a nested def's
    # parameters are bound as its locals.
    param_type: TpyType | None = None
    # `T*`: a read without a deref is the raw pointer, which only a whole
    # lift consumes.
    pointer: bool = False
    # A first local declaration's C++ shape where the borrow-local rows
    # decide it (`T&`, `T*`, a rebind slot, an Optional pointer slot or
    # lift): the declaration arm builds that shape.
    local_binding: 'LocalBinding | None' = None
    # A pointer whose rvalue reseats go through a pre-declared slot.
    rebind_slot: bool = False
    # `T&`.
    ref_alias: bool = False
    # `::tpy::Union<A*, B*>`.
    ptr_variant: bool = False
    # A value-repr `std::optional<T>`, by the kind its narrowed deref reads.
    value_opt: 'ValueOptKind | None' = None
    # A storage-form `std::optional<P>` of a pointer-repr Optional.
    storage_opt: bool = False
    const_storage_opt: bool = False
    # A pointer-repr tuple held in storage form (`std::tuple<A, B>`).
    storage_tuple: bool = False
    const_storage_tuple: bool = False
    # The mixed own / borrow render of a per-element-Own tuple.
    borrow_tuple_mixed: bool = False
    # `std::optional<std::tuple<.., T*>>`.
    optional_borrow_tuple: bool = False
    # An owned tuple constructed in place, and its element layout.
    tuple_layout: 'THIRTupleLayout | None' = None
    # Per element of a borrow tuple: held inline rather than by pointer.
    inline_elems: tuple[bool, ...] | None = None
    # A const declaration (the decl families; a parameter's const is its
    # reading function's verdict).
    const: bool = False
    # A loop variable bound const.
    const_loop_var: bool = False
    # The whole-body borrow-tuple const fixpoint, per NAME as the lowering
    # keeps it: every record of a marked name carries the bit.
    const_borrow_tuple: bool = False
    const_opt_borrow_tuple: bool = False
    # A sink may move from it at its last use (outside a `moves_only`
    # region, which decides there).
    movable: bool = False
    # A resumable frame's `tpy::frame_slot<T>` field.
    frame_slot: bool = False
    # A `std::optional<T>` holding a pointer-repr Optional's value (an
    # OPTIONAL_STORAGE hoist, an `Own[P | None]` param's
    # `std::optional<P>&&`): read through as the `T*` it holds, assigned
    # plainly, tested with `has_value`.
    optional_storage: bool = False
    # The concrete coroutine frame in `std::optional<__coro_f>`, its
    # erasure deferred to the `Own[dyn]` consumer: a reseat emplaces.
    coro_frame: bool = False
    # A reassigned pointer local lifted off the `std::optional<P> __slot_N`
    # an owning call fills: a reseat re-fills that slot.
    opt_storage_call: bool = False
    # An `auto` iterator object off a generator call, or an `auto&` second
    # name for one.
    iterator_object: bool = False
    # An `Own[...]` parameter: the caller handed the value over.
    owned: bool = False
    # A frame field's layout facts the declared type cannot show.
    effective_type: TpyType | None = None
    payload: str | None = None
    global_cpp: str | None = None
    # A pointer-slot module global, read as the pointer it is.
    global_slot: bool = False
    # A nested def's name: the lowering binds it as a closure local
    # (`lc.nested_def_locals`), never in `declared`.
    nested_def: bool = False


# The record a name with no binding answers with (a function, a class, a
# synthesized narrowing alias): every representation fact false.
UNBOUND = BindingRepr(name="", site=None, type=None, row="unbound")


class NameFacts:
    """The names whose binding has one representation fact, for a helper
    that takes a name set: membership asks the record. It cannot be
    iterated -- the table answers per name, never by listing."""
    __slots__ = ("_test",)

    def __init__(self, test: Callable[[str], bool]) -> None:
        self._test = test

    def __contains__(self, name) -> bool:
        return isinstance(name, str) and self._test(name)

    def __or__(self, other: AbstractSet[str]) -> 'NameFacts':
        return NameFacts(lambda n: n in self or n in other)

    __ror__ = __or__

    def __sub__(self, other: AbstractSet[str]) -> 'NameFacts':
        return NameFacts(lambda n: n in self and n not in other)

    def __iter__(self):
        raise TypeError("NameFacts answers membership only")


@dataclass(frozen=True, slots=True)
class Source:
    held: Held
    # A NAME read whose representation turns on whether it takes the
    # binding's narrowed unwrap (a value-optional's `(*x)`): `held` read
    # whole and through the unwrap, so a later deref flip on the node
    # (`THIRExpr._restamp_reads`) re-picks it. None where the two agree.
    held_by_deref: 'tuple[Held, Held] | None' = None
    # The ACTUAL const-ness of what the expression denotes (a const param,
    # a const local, a member or element of one) -- never what a binding
    # asks for.
    const: bool = False
    # A sink may move the value out of the storage it names: an owned name
    # or `self` at sema's last use, movable where the sink sits. A FRESH
    # value is an rvalue already and needs no move.
    movable: bool = False
    # The C++ value is a raw pointer (`T*`): a pointer binding read without
    # its deref, a borrowed element of a pointer-repr tuple. On a node it is
    # the node's own `reads_raw_pointer`, rewritten whenever the node is
    # (re)built, so a later deref flip cannot leave it stale.
    pointer_held: bool = False
    # The read renders as the binding's own name -- a C++ id-expression,
    # which a `return` moves implicitly -- not a deref or an unwrap of it.
    # The node's own `reads_binding`, kept current like `pointer_held`.
    id_read: bool = False
    # Sema declared a copy of this read (a returned name a closure still
    # reads): the value must be copied, never moved, wherever it lands.
    must_copy: bool = False
    # The value dies at the end of its full expression: an rvalue
    # (`value_category.is_rvalue_source`) or a member / element read off
    # one. Nothing may keep a reference to it. None is unknown -- a node
    # built with no parse node whose kind says nothing -- and is never
    # read as durable.
    temporary: bool | None = False
    # Per element of a tuple-typed literal or name, else None.
    elems: 'tuple[Source, ...] | None' = None
    # The binding a NAME read reads; None for every other expression.
    binding: BindingRepr | None = None
    # The type sema analyzed the source expression at (`get_expr_type`),
    # before lowering settled a still-pending literal type.
    analyzed_type: TpyType | None = None
    # The type a copy of this value constructs -- the source's own type,
    # never the slot's: a slot-typed copy of a subclass direct-initializes
    # the base and can pick a user constructor taking the subclass. Set for
    # a record-like value and for a read sema declared a copy of; the sink
    # spells it.
    copy_type: TpyType | None = None
