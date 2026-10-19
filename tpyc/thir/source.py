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
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

from ..typesys import TpyType

if TYPE_CHECKING:
    from ..codegen_cpp.forms import LoopBinding
    from ..typesys import TupleType
    from .lower.context import FrameLocalClass, ValueOptKind
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


class BindingForm(Enum):
    """How a binding's C++ holds its value, decided once by the binding
    planner (`lower/bindings.py`). The declaring site (`site_kind`), the
    const and owning-slot facts and the initializer's construction
    (`BindingConstruct`) sit beside it on the record."""
    # No binding-specific form: the type's own C++ (a value local, a
    # parameter as its signature passes it, `self`). A site that needs a
    # form (a hoist, an unpack, a walrus, a with target) rejects on it.
    NONE = "none"
    # The object itself (`T x = ..`, `T x;`, `auto x`).
    VALUE = "value"
    # `auto&` / `auto&&` / `const T&`.
    REFERENCE = "reference"
    # `T*`, nullable included.
    POINTER = "pointer"
    # `::tpy::Union<A*, B*>`.
    POINTER_UNION = "pointer_union"
    # A value-repr `std::optional<T>` (its kind in `value_opt`).
    VALUE_OPT = "value_opt"
    # The storage `std::optional<P>` of a pointer-repr Optional, read as
    # storage.
    STORAGE_OPT = "storage_opt"
    # An owned `std::optional<T>` slot.
    OWNED_SLOT = "owned_slot"
    # `std::tuple<.., T*>` (the mixed own / borrow render:
    # `borrow_tuple_mixed`).
    BORROW_TUPLE = "borrow_tuple"
    # `std::optional<std::tuple<.., T*>>`.
    OPT_BORROW_TUPLE = "opt_borrow_tuple"
    # `std::tuple<A, B>` in storage form.
    STORAGE_TUPLE = "storage_tuple"
    # A `@dynamic` local behind its base pointer, or its coroutine frame.
    DYN = "dyn"
    # A resumable frame's `tpy::frame_slot<T>` field.
    FRAME_SLOT = "frame_slot"


class BindingConstruct(Enum):
    """How a first declaration's, a walrus's first binding's or a fresh
    unpack target's initializer becomes its `BindingForm`, where one form
    has several constructions: read only by the arms that build the
    declaration from it (the first declaration builders,
    `bindings.decl_shape`, the walrus arm, the unpack binds). A walrus
    takes `POINTER`, `OPT_PTR` and `BTUPLE_REBOUND`; a fresh unpack target
    the unpack members below, `OPT_PTR` and `PTR_VARIANT`, each rendered
    by its `UnpackBind` (`bindings.unpack_tag`)."""
    # First declarations.
    NO_INIT_OPT_PTR = "no_init_opt_ptr"
    NO_INIT_VALUE = "no_init_value"
    CLOSED_FRAME = "closed_frame"
    ER_ALIAS = "er_alias"
    ER_BIND = "er_bind"
    OWN_ELEM_TUPLE = "own_elem_tuple"
    RECORD_SLOT = "record_slot"
    RECORD_HOISTED_SLOT = "record_hoisted_slot"
    CONTAINER_SLOT = "container_slot"
    STORAGE_OPT_LIFT = "storage_opt_lift"
    DYN_OWN_ERASED = "dyn_own_erased"
    CORO_FRAME_LOCAL = "coro_frame_local"
    DYN = "dyn"
    OPT_BTUPLE = "opt_btuple"
    REF_ALIAS = "ref_alias"
    POINTER = "pointer"
    REBIND_SLOT = "rebind_slot"
    OPT_CALL_PASSTHROUGH = "opt_call_passthrough"
    OPT_STORAGE_CALL = "opt_storage_call"
    OPT_PTR = "opt_ptr"
    OPTIONAL_TO_PTR = "optional_to_ptr"
    OPT_OWN_PARAM_LIFT = "opt_own_param_lift"
    MIXED_ELEM_PTR = "mixed_elem_ptr"
    STORAGE_TUPLE_ALIAS = "storage_tuple_alias"
    STORAGE_TUPLE_CHAIN = "storage_tuple_chain"
    COMPREHENSION = "comprehension"
    TPARAM_CALL = "tparam_call"
    PTR_VARIANT = "ptr_variant"
    PTR_VARIANT_SLOT = "ptr_variant_slot"
    STORAGE_CALL_TUPLE = "storage_call_tuple"
    ITERATOR_OBJECT = "iterator_object"
    BTUPLE_REBOUND = "btuple_rebound"
    BTUPLE_ALIAS = "btuple_alias"
    BTUPLE_REF_ALIAS = "btuple_ref_alias"
    BTUPLE_INLINE_ARMS = "btuple_inline_arms"
    OPTVIEW_SHIM = "optview_shim"
    BTUPLE_LITERAL = "btuple_literal"
    TUPLE_LITERAL_WIDE = "tuple_literal_wide"
    TUPLE_LITERAL_RECORD = "tuple_literal_record"
    OPT_RECORD_CALL = "opt_record_call"
    VALUE_OPT = "value_opt"
    OPT_VALUE_RECORD = "opt_value_record"
    OWNED = "owned"
    # Fresh unpack targets: the `unwrap_ref` alias of a recursive
    # wrapper's live `X&`, the `auto&&` alias through an element pointer,
    # the `const T&` of a value element, the rebound `auto*`, the owned
    # element moved out of the holder, the copy.
    UNWRAP_REF = "unwrap_ref"
    REF = "ref"
    CREF = "cref"
    PTR = "ptr"
    MOVE = "move"
    VALUE = "value"


class BindingRefinement(Enum):
    """A record re-derived from an earlier one at a later site, which keeps
    that record's form: a whole-Optional match capture into it, a frame
    hoist's value-opt kind, a standalone unpack's move promotion, a nested
    def's view of an enclosing global slot."""
    MATCH_BIND = "match_bind"
    FRAME_HOIST = "frame_hoist"
    UNPACK_PROMOTE = "unpack_promote"
    NESTED_DEF = "nested_def"


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
    # Its C++ representation, and how its initializer constructs it.
    form: BindingForm
    construct: BindingConstruct | None = None
    # A for head's null-pointer hoist of a container unpack target: the
    # head re-points it each pass.
    head_target: bool = False
    # The kind of site that declared it (`_Site.kind` in lower/bindings.py:
    # 'decl', 'hoist', 'loop', 'param', 'receiver', 'frame', ...); None for
    # `UNBOUND`. A refinement keeps it.
    site_kind: str | None = None
    # The later sites that re-derived it, in order (the form stays).
    refinements: tuple[BindingRefinement, ...] = ()
    is_param: bool = False
    # The type the parameter list the body is lowered under (`lc.params`)
    # gives the name, `Ref[...]` and all: set for the function's own
    # parameters and, inside a lambda, the lambda's; a nested def's
    # parameters are bound as its locals.
    param_type: TpyType | None = None
    # `T*`: a read without a deref is the raw pointer, which only a whole
    # lift consumes.
    pointer: bool = False
    # A rebound reference local held as a pointer over storage of its own
    # (the storage policy, `first_binding_storage`): a rebind re-points it.
    rebind_slot: bool = False
    # That storage stands before the loop the binding re-runs in (the
    # escape hoist: an alias of it is kept across passes).
    storage_hoisted: bool = False
    # A hoisted pointer without a rebind slot of its own: an rvalue reseat
    # fills one of its own, declared beside the predeclaration
    # (BRANCH_RVALUE).
    lazy_rebind_slot: bool = False
    # A match capture's borrow-only pointer hoist (`T* q;`, `const T*`
    # where a nested match re-seats it off a const subject): a capture
    # into it, this match's or a nested one's, assigns its address.
    borrowed_capture: bool = False
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
    # An open-T tuple local no binding row took, declared as the storage copy
    # it compiles to at a value T (`std::tuple<T, int32_t> y = ...;`)
    # (BUGS.md#open-t-tuple-reseat-storage-residue).
    open_t_storage_copy: bool = False
    # An owned tuple constructed in place, and its element layout.
    tuple_layout: 'THIRTupleLayout | None' = None
    # Per element of a borrow tuple: held inline rather than by pointer.
    inline_elems: tuple[bool, ...] | None = None
    # The pointer tuple (`std::tuple<.., T*>`) a rebound tuple local is at
    # every binding when some binding owns a reference element: the
    # declared type's elements, `Own` collapsed, each owned element backed
    # by a reference local's slot at its own site. Whether a read may move
    # an element is the read's (`own_element_copies`).
    ptr_tuple: 'TupleType | None' = None
    # A const binding: a const declaration (the decl families; a borrow
    # tuple's element pointers), a parameter under its function's const
    # verdict (the one its signature spells), a read-only method's
    # receiver. A nested scope reads an enclosing parameter's or
    # receiver's record, so its verdict.
    const: bool = False
    # A parameter whose INNER surface is const beyond `const`: the deep
    # const verdict (a pointer-variant's pointees), an unmutated `*args`
    # pack's elements, a `Ptr[readonly[T]]` pointee.
    deep_const: bool = False
    # Some value bound into this record is a const borrow (the planner's
    # const fixpoint). A declaration, hoist or walrus pointer slot or
    # borrow tuple holding one is `const T*` (`const`): a slot holding a
    # mutable source too rejects at planning.
    const_source: bool = False
    # A loop variable bound const: a const reference, or a reference over
    # an iteration that yields const elements (`auto&&` deduces const).
    const_loop_var: bool = False
    # How a loop or comprehension clause binds its variable (a for
    # statement's variable or tuple-unpack holder, a clause's variable or
    # holder): the C++ the loop emits per element. None on every other
    # record.
    loop_binding: 'LoopBinding | None' = None
    # A sink may move from it at its last use (outside a `moves_only`
    # region, which decides there).
    movable: bool = False
    # What a resumable frame member is (`frame_local_class`; a frame's
    # parameter is PLAIN): None for a binding the frame does not hold.
    frame_class: 'FrameLocalClass | None' = None
    # A binding the frame holds to const storage (the frame layout's own
    # `const_frame_bindings`: const-borrow captures, a `@readonly` receiver,
    # const alias fields, loop holders).
    frame_const: bool = False
    # A frame pointer field that borrows its pointee (a loop variable, an
    # unpack target, a statement alias): the frame owns nothing behind it.
    frame_borrowed: bool = False
    # A frame loop's pointer-form variable over an ITERATOR source: it
    # points into the step result, whose members may themselves be
    # references or pointers (a proxy tuple), so targets unpacked off it
    # re-point through the normalizing deref.
    source_typed_holder: bool = False
    # A frame loop's pointer-repr Optional variable: the nullable `T*`
    # points into the iterated container, so the frame outlives what it
    # lends.
    opt_ptr_loop_var: bool = False
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
    # A module-init global the module itself declares and writes (not an
    # imported one, not a `Final`); False outside module init.
    own_global: bool = False
    # A nested def's name: the lowering binds it as a closure local, never
    # in `declared`.
    nested_def: bool = False

    @property
    def label(self) -> str:
        """`<site kind>.<form>[.<construct>]`, for internal-error text."""
        out = f"{self.site_kind}.{self.form.value}"
        return (out if self.construct is None
                else f"{out}.{self.construct.value}")

    @property
    def frame_slot(self) -> bool:
        """A resumable frame's `tpy::frame_slot<T>` field."""
        return self.form is BindingForm.FRAME_SLOT

    @property
    def optional_borrow_tuple(self) -> bool:
        """`std::optional<std::tuple<.., T*>>`."""
        return self.form is BindingForm.OPT_BORROW_TUPLE

    @property
    def module_slot(self) -> bool:
        """A pointer-slot global of the module-init carrier's own module: a
        write there allocates its `static __global_slot_N`, and it reads as
        the bare pointer whatever its spelling. An imported one is never
        written in this module's init."""
        return self.global_slot and self.own_global


@dataclass(frozen=True)
class SlotKey:
    """One record's C++ slot -- a pointer slot the const fixpoint judges, a
    rebind slot the emitter numbers: the site declaring it and the name (a
    hoist site declares several). Compared by the site's identity; the key
    holds the site so the identity cannot be recycled. A refinement keeps
    its record's site, so its key."""
    site_id: int
    name: str
    site: object = field(compare=False, repr=False)

    @staticmethod
    def of(site: object, name: str) -> 'SlotKey':
        return SlotKey(id(site), name, site)


def slot_key(rec: 'BindingRepr') -> SlotKey:
    return SlotKey.of(rec.site, rec.name)


# The record a name with no binding answers with (a function, a class, a
# synthesized narrowing alias): every representation fact false.
UNBOUND = BindingRepr(name="", site=None, type=None, form=BindingForm.NONE)


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
    # A pointer element of a pointer-tuple NAME read at its last use that
    # sema declared no owning-sink verdict for (`declared_tuple_reads`): it
    # is not movable, and a sink that would move it refuses.
    undeclared: bool = False
