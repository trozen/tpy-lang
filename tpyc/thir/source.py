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

from dataclasses import dataclass
from enum import Enum, auto

from ..typesys import TpyType


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


@dataclass(frozen=True, slots=True)
class Binding:
    """The local / parameter / global a NAME read reads, as the read sees
    it. `type` is the binding's declared type (a flow-narrowed read has a
    narrower `result_type`); `param_type` is set for a parameter of the
    function being lowered."""
    name: str
    type: TpyType | None
    param_type: TpyType | None
    is_param: bool
    # The binding is a pointer (`T*`): its read without a deref is the raw
    # pointer, which only a whole lift consumes.
    pointer: bool
    # A pointer-slot module global, read as the pointer it is.
    global_slot: bool


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
    binding: Binding | None = None
    # The type sema analyzed the source expression at (`get_expr_type`),
    # before lowering settled a still-pending literal type.
    analyzed_type: TpyType | None = None
    # The type a copy of this value constructs -- the source's own type,
    # never the slot's: a slot-typed copy of a subclass direct-initializes
    # the base and can pick a user constructor taking the subclass. Set for
    # a record-like value and for a read sema declared a copy of; the sink
    # spells it.
    copy_type: TpyType | None = None
