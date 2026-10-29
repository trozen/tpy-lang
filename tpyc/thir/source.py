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
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import TYPE_CHECKING

from ..sema.alias_rebind import bare_tuple
from ..typesys import (OwnType, TpyType, TupleType, UnionType, unwrap_readonly,
                       unwrap_ref_type, unwrap_send_sync)

if TYPE_CHECKING:
    from ..codegen_cpp.forms import LoopBinding
    from ..sema.context import ElemPath
    from ..sema.source_records import SourceRecord
    from .lower.context import FrameLocalClass, ValueOptKind
    from .nodes import THIRTupleLayout


class TraitAbi(Enum):
    """The template an open `T` is spelled through where its instantiation
    decides by value or by reference: a result's `val_or_ref_t`, a
    parameter's `param_val_or_ref_t`, a tuple element's (or a resumable
    payload's) `val_or_ptr_t`. A slot's (`Slot.trait`) and a tuple source
    leaf's (`Source.trait`)."""
    VAL_OR_REF = "val_or_ref_t"
    PARAM_VAL_OR_REF = "param_val_or_ref_t"
    VAL_OR_PTR = "val_or_ptr_t"
    # A generator's yield slot (`yield_slot_t`): the frame member it reads
    # is already the instantiation's `val_or_ref_t`, so it takes it bare.
    YIELD_SLOT = "yield_slot_t"


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


def path_permitted(path: 'ElemPath',
                   permitted: 'frozenset[ElemPath]') -> bool:
    """Whether a copy of the leaf at `path` lies under a permitted path."""
    return any(path[:len(p)] == p for p in permitted)


def copy_permitted(s: 'Source') -> 'frozenset[ElemPath]':
    """The leaf paths of this existing object sema permits a copy of (its
    record's `SourceRecord.copy_permitted`)."""
    return s.record.copy_permitted if s.record is not None else frozenset()


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
    """How a first declaration's or a walrus's first binding's initializer
    becomes its `BindingForm`, where one form has several constructions:
    read only by the arms that build the declaration from it (the first
    declaration builders, `bindings.decl_shape`, the walrus arm). A walrus
    takes `POINTER`, `OPT_PTR` and `BTUPLE_REBOUND`. A fresh unpack target
    has none: it is written as its scalar binding is, by its form."""
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


class BindingRefinement(Enum):
    """A record re-derived from an earlier one at a later site, which keeps
    that record's form: a whole-Optional match capture into it, a frame
    hoist's value-opt kind, a standalone unpack's move promotion, a nested
    def's view of an enclosing global slot."""
    MATCH_BIND = "match_bind"
    FRAME_HOIST = "frame_hoist"
    UNPACK_PROMOTE = "unpack_promote"
    NESTED_DEF = "nested_def"


class MoveRow(Enum):
    """A planner row where a record's `movable` is not its binding's
    ownership (`sema.binding_ownership`): `movable` is `forced(rec) or
    (owned and not exception(rec))`. An EXCEPTION is an owned binding no
    read moves out of through this representation; a FORCED row is moved
    out of whatever the binding owns. Each names its reason, and its
    `slug` the BUGS.md entry where the representation is a defect."""
    # Exceptions: moving equals copying.
    TRIVIAL_VALUE = "trivial_value"
    # A buffered value's render copies at its last use (a hidden cost).
    VIEWFAM_VALUE = "viewfam_value"
    OWNED_VALUE = "owned_value"
    # A comprehension variable moves only at its sinks, never through the
    # binding: a filter read must not move it.
    COMP_CLAUSE = "comp_clause"
    # A started generator frame is not moved.
    GENERATOR_OBJECT = "generator_object"
    # A `@dynamic` local or deferred coroutine frame read by an `Own` slot.
    DYN = "dyn"
    # A frame field the frame layout does not move: a borrowed pointer, a
    # field no declaration binds.
    FRAME_LAYOUT = "frame_layout"
    # A lambda parameter: its return moves (the C++ moves a returned
    # parameter), any other read copies.
    LAMBDA_PARAM = "lambda_param"
    # A pass slot: a hoisted loop variable, a loop's tuple-unpack holder.
    PASS_SLOT = "pass_slot"
    # A binding that refers to existing storage, a row only where fact 1
    # says it owns: an all-fresh Optional select planned as a call
    # passthrough, which the lowering refuses (`expr.ifexpr`).
    ALIAS = "alias"
    # Module storage, a row only where fact 1 says the binding owns: the
    # planner reads a name as a module global (`binds_global`) that sema
    # did not declare global, so the name stays unmoved.
    MODULE_STORAGE = "module_storage"
    # A const pointer slot.
    CONST_SLOT = "const_slot"
    # A binding with no form of its own.
    NO_FORM = "no_form"
    # Forced: a closed frame's optional slot.
    CLOSED_FRAME = "closed_frame"
    # Forced: an owned-slot hoist moves while its declaration twin rejects.
    OWNED_SLOT = "owned_slot"
    # Forced: a by-value Optional parameter (moving a view is a copy).
    BY_VALUE_OPTIONAL = "by_value_optional"
    # Forced: a standalone unpack's promotion of an owned local.
    UNPACK_PROMOTE = "unpack_promote"
    # Forced: a loop / clause variable inherits the outer binding's.
    INHERITED_OUTER = "inherited_outer"

    @property
    def forced(self) -> bool:
        return self in _FORCED_MOVE_ROWS

    @property
    def record_moves(self) -> bool:
        """Whether an owning sink's record may move a read of a binding
        this row excepts. A value row may (moving equals copying, or is the
        cheaper render the row's copy stands in for), and so may a
        generator object (its consumers take the frame in place, so no
        move renders); a lambda parameter only as the lambda's own result,
        which its result slot decides (`convert`). Every other row --
        `DYN`, `ALIAS` and `MODULE_STORAGE` included -- is no move a record
        may ask for: one reaching it is a compiler bug
        (`check_record_move`)."""
        return self in _RECORD_MOVE_ROWS

    @property
    def slug(self) -> str | None:
        """The BUGS.md entry tracking the row, None where it is correct."""
        ref = _MOVE_ROW_SLUGS.get(self)
        return None if ref is None else ref.removeprefix("BUGS.md#")


_FORCED_MOVE_ROWS = frozenset({
    MoveRow.CLOSED_FRAME, MoveRow.OWNED_SLOT, MoveRow.BY_VALUE_OPTIONAL,
    MoveRow.UNPACK_PROMOTE, MoveRow.INHERITED_OUTER})
_RECORD_MOVE_ROWS = frozenset({
    MoveRow.TRIVIAL_VALUE, MoveRow.VIEWFAM_VALUE, MoveRow.OWNED_VALUE,
    MoveRow.GENERATOR_OBJECT, MoveRow.LAMBDA_PARAM})
_MOVE_ROW_SLUGS: dict[MoveRow, str] = {
    MoveRow.VIEWFAM_VALUE: "BUGS.md#owned-viewfam-local-last-use-copies",
    MoveRow.OWNED_VALUE: "BUGS.md#owned-value-local-last-use-copies",
    MoveRow.GENERATOR_OBJECT: "BUGS.md#generator-into-own-sink-rejects",
    MoveRow.DYN: "BUGS.md#dyn-local-own-arg-ill-formed",
    MoveRow.OWNED_SLOT: "BUGS.md#generator-into-own-sink-rejects",
}


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
    # Every binding of the name in its function puts a fresh value of its
    # own there (sema's `BindKind.RVALUE`), so a pointer local always points
    # at storage it owns; a name any other binding reaches (a place, an
    # unpack / loop / match / with / except target, a parameter, an
    # augmented or nonlocal rebind) may point at storage another binding
    # owns. Order-free: the planner reads every binding of the body.
    fresh_only: bool = False
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
    # an element is the read's (its record, `SourceRecord.element_moves`).
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
    # region, which decides there): `move_forced`, or the binding owns what
    # it holds (`sema.binding_ownership`) and no `move_exception` stands.
    movable: bool = False
    move_exception: 'MoveRow | None' = None
    move_forced: 'MoveRow | None' = None
    # What a resumable frame member is (`frame_local_class`; a frame's
    # parameter is PLAIN): None for a binding the frame does not hold.
    frame_class: 'FrameLocalClass | None' = None
    # A binding the frame holds to const storage (the frame layout's own
    # `const_frame_bindings`: const-borrow captures, a `@readonly` receiver,
    # const alias fields, loop holders).
    frame_const: bool = False
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
    # A `str` / `bytes` parameter (or its Optional) a resumable frame
    # captured into owned storage of its own (`is_owned_in_coro_frame`):
    # a read is the frame's buffer, not the caller's view.
    frame_owned_view: bool = False
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
    def reads_as_pointer(self) -> bool:
        """The bare name is a `T*`: a pointer record whose storage is not an
        owning `std::optional<T>`. A hoist's owned slot is engaged whenever
        it is read, so it is the object, addressed as `&((*x))`; an
        `Own[P | None]` parameter may be empty (`nullable_storage`)."""
        return self.pointer and not self.optional_storage

    @property
    def nullable_storage(self) -> bool:
        """A `std::optional<P>` storage of a pointer-repr Optional that may
        be empty -- a storage-optional unpack target, an `Own[P | None]`
        parameter (`std::optional<P>&&`): its `T*` is
        `::tpy::optional_to_ptr(x)`, never the object addressed."""
        return self.storage_opt or (self.optional_storage and self.owned)

    @property
    def owned_slot_object(self) -> bool:
        """An owned `std::optional<T>` slot read as the object it holds (a
        walrus's, a hoist's), through its deref -- not a rebound record's
        slot of its own, which reads as the `T*` aiming at it."""
        return (self.form is BindingForm.OWNED_SLOT
                and not self.reads_as_pointer)

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
    # A NAME or FIELD read whose representation turns on whether it takes
    # its storage's narrowed unwrap (a value-optional's `(*x)`, a narrowed
    # Optional member's `(*h.f)`): `held` read whole and through the
    # unwrap, so a later deref flip on the node (`THIRExpr._restamp_reads`)
    # re-picks it. None where the two agree.
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
    # A NAME read of an enclosing body's binding (a nested def's or a
    # lambda's capture): no object of the frame the read sits in.
    captured: bool = False
    # The declared type of the storage a NAME or FIELD read names, before
    # flow narrowing (a narrowed Optional member's whole Optional); None
    # for every other expression.
    named_type: TpyType | None = None
    # The type sema analyzed the source expression at (`get_expr_type`),
    # before lowering settled a still-pending literal type.
    analyzed_type: TpyType | None = None
    # The record sema published for the read (`read_record`), an
    # element's its whole's under it (`SourceRecord.under`); None where it
    # published none, or the Source was built without asking.
    record: 'SourceRecord | None' = None
    # The type a copy of this value constructs -- the source's own type,
    # never the slot's: a slot-typed copy of a subclass direct-initializes
    # the base and can pick a user constructor taking the subclass. Set for
    # a record-like value and for a read sema declared a copy of; the sink
    # spells it.
    copy_type: TpyType | None = None
    # A tuple source's LEAF (`elems[i]`, built by `element_source`) held
    # through an open `T`'s trait (`val_or_ptr_t<T>`): a pointer or a value
    # as the instantiation decides. None for a leaf held inline or as a bare
    # pointer (`pointer_held`), and for every whole value.
    trait: 'TraitAbi | None' = None
    # A pointer LEAF's pointee is owned by the read, so a sink may take it
    # (`_read_owned_elements`) -- what the pointee IS, never whether this
    # occurrence may move it (`movable`). None is unknown, which grants no
    # move.
    pointee_owned: bool | None = None
    # The C++ expression is a prvalue: a value no name refers to (a
    # by-value call result, a construction, a select of such), which a
    # conversion holding it once may take apart. False is unknown -- an
    # lvalue, or a reference into storage the expression does not own.
    prvalue: bool = False
    # The expression names a place a second evaluation names again, at no
    # cost and running no code of the program: a name, a member or tuple
    # element of one, a builtin container's element by a name or literal
    # index. A conversion reads such a source once per element.
    place: bool = False

    @property
    def name(self) -> str | None:
        """The name of the binding a NAME read reads."""
        return self.binding.name if self.binding is not None else None

    @property
    def move_exception(self) -> 'MoveRow | None':
        """The planner row excepting the binding a NAME read reads from a
        move (`BindingRepr.move_exception`)."""
        return (self.binding.move_exception if self.binding is not None
                else None)

    @property
    def param_type(self) -> TpyType | None:
        """The declared type of the parameter a NAME read reads."""
        return self.binding.param_type if self.binding is not None else None

    @property
    def owned_param(self) -> bool:
        """A NAME read of this body's own `Own[...]` parameter."""
        return (self.binding is not None and self.binding.owned
                and not self.captured)

    @property
    def owned_result_storage(self) -> bool:
        """A NAME read of an `Own[P | None]` parameter, this body's or a
        captured one: its binding is a result's storage itself
        (`std::optional<P>&&`)."""
        return (self.binding is not None and self.binding.owned
                and self.binding.nullable_storage)


def _element_type(t: 'TpyType | None', index: int) -> 'TpyType | None':
    """Element `index` of a tuple type as declared (`Own` kept), None for
    no tuple."""
    tup = bare_tuple(t)
    if tup is not None and index < len(tup.element_types):
        return tup.element_types[index]
    return None


def element_source(parent: Source, index: int, *,
                   held: 'Held | None' = None, pointer: bool = False,
                   trait: 'TraitAbi | None' = None,
                   const: bool = False, movable: bool = False,
                   pointee_owned: 'bool | None' = None,
                   own: 'Source | None' = None) -> Source:
    """THE constructor of a tuple source's leaf (`Source.elems[index]`):
    the element projected from its `parent` Source plus the layout's answer
    for it -- `held` where it lives, a bare `pointer` into storage held
    elsewhere, or an open `T` spelled through `trait`, else inline.

    An inline (or trait) leaf is part of the parent's object: const where
    the parent is, dying with it. A pointer leaf's const is its pointee's
    (`const`, the layout's), and its pointee's lifetime is unknown here.
    Either is captured where the parent is, and must be copied where the
    whole must. A leaf the parent builds from an expression of its own (a
    literal's element) is that expression's Source (`own`), with the
    layout's representation.

    What the layout does not state is unknown and grants no move
    (`movable`, `pointee_owned` None). The leaf's record is the parent's
    under it (`SourceRecord.under`)."""
    if own is not None:
        # An element the literal holds inline is its storage, whatever form
        # the expression it was built from reads in.
        inline_borrow = (own.held is Held.BORROWED and not pointer
                         and not own.pointer_held)
        return replace(own, pointer_held=own.pointer_held or pointer,
                       held=Held.STORAGE if inline_borrow else own.held,
                       trait=trait, pointee_owned=pointee_owned)
    assert held is not None, "a projected leaf names where it lives"
    inline = not pointer
    return Source(held=held, pointer_held=pointer, trait=trait,
                  const=const or (inline and parent.const),
                  movable=movable, pointee_owned=pointee_owned,
                  must_copy=parent.must_copy,
                  record=(parent.record.under(index)
                          if parent.record is not None else None),
                  captured=parent.captured,
                  temporary=parent.temporary if inline else None,
                  named_type=_element_type(parent.named_type, index),
                  analyzed_type=_element_type(parent.analyzed_type, index))


def elem_borrow_form_differs(et: TpyType) -> bool:
    """Whether a tuple element's borrow form is a representation of its own
    beside its storage: a pointer-variant union, or a nested tuple some
    element of which differs (`TupleType.to_cpp_return` spells both)."""
    core = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
    if isinstance(core, UnionType):
        return core.uses_pointer_repr()
    if isinstance(core, TupleType):
        return any(TupleType._element_is_pointer_repr(e)
                   or TupleType._element_is_val_or_ptr(e)
                   or elem_borrow_form_differs(e)
                   for e in core.element_types)
    return False


def tuple_elems(whole: Source, src_t: 'TupleType', *,
                dead: bool = False) -> tuple[Source, ...]:
    """Each element of a tuple source as the source holds it: the
    per-element facts its read stamped (`Source.elems`), else projected
    from its element TYPE (`Own` kept) in the representation the whole is
    held in -- a borrow-form tuple (`to_cpp_return`) holds a reference
    element as a pointer, an open `T` through `val_or_ptr_t`, a union as
    its pointer variant and a nested tuple in its own borrow form; any
    other form holds each element inline. `dead`: the whole is a temporary
    nothing reads after this, so an element it holds inline may move out
    of it; a pointee it only points at is never its to move."""
    n = len(src_t.element_types)
    if whole.elems is not None and len(whole.elems) == n:
        return whole.elems
    # A tuple parameter holds every element in its passing form, whatever
    # the whole's type alone says (`TupleType._param_spelling`) -- unless
    # the parameter takes the whole tuple's storage (`Own[tuple[...]]`).
    param = whole.binding.param_type if whole.binding is not None else None
    # A binding holding the mixed result form (`std::tuple<A, B*>` for
    # `tuple[Own[A], B]`) lends its non-`Own` elements; a wholly owned
    # tuple of the same element types (`Own[tuple[Own[A], B]]`) does not.
    borrow = whole.held is Held.BORROWED or (
        whole.binding is not None and whole.binding.borrow_tuple_mixed) or (
        whole.binding is not None and whole.binding.is_param
        and not isinstance(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            param))) if param is not None else None, OwnType))
    # A borrow tuple binding holding some element inline (a literal's fresh
    # member) holds it as its storage, whatever its type's borrow form.
    inline = (whole.binding.inline_elems if whole.binding is not None
              else None)
    out: list[Source] = []
    for i, et in enumerate(src_t.element_types):
        if inline is not None and i < len(inline) and inline[i]:
            out.append(element_source(whole, i, held=Held.STORAGE,
                                      movable=dead or whole.movable))
        elif borrow and TupleType._element_is_pointer_repr(et):
            out.append(element_source(whole, i, held=Held.BORROWED,
                                      pointer=True, const=whole.const,
                                      pointee_owned=False))
        elif borrow and TupleType._element_is_val_or_ptr(et):
            out.append(element_source(whole, i, held=Held.BORROWED,
                                      trait=TraitAbi.VAL_OR_PTR))
        elif borrow and elem_borrow_form_differs(et):
            out.append(element_source(whole, i, held=Held.BORROWED))
        else:
            out.append(element_source(whole, i, held=Held.STORAGE,
                                      movable=dead or whole.movable))
    return tuple(out)
