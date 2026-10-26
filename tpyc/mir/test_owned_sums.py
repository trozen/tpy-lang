"""A by-value `std::optional<T>` is one owned sum slot: built empty where C++
declares it, filled from a record its full expression built, refilled in
place; a record it stores inline keys its stored loans under the payload."""

import re
from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..compiler import Compiler
from ..thir.testutil import _compile, _entry
from ..typesys import INT32, OptionalType, STRVIEW, TupleType
from .dependencies import MIRReferent, object_keys, stored_on_fill
from .collect import enumerate_bodies
from .nodes import (
    MIRAssign, MIRBlockId, MIRBodyId, MIRBorrow, MIRConstruct, MIRCopy, MIRFunction, MIRPlace, MIRPoint,
    MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration, MIRStorageInit, MIRWrap,
)
from .retention import MIRRetention, analyze_retention
from .shapes import MIRTupleIndex, MIRUnionPayload
from .testutil import hand_record, leaf_shape, optional_shape, owned_shape, prepared, records_of, tuple_shape
from .prepare import validate_function
from .value import MIRField, MIRFieldId, MIRRecordLayout, MIRValidationError, shape_problem


SOURCE = '''from tpy import Own, StrView, int32, nocopy

class P:
    x: int32
    def __init__(self, x: int32):
        self.x = x

@nocopy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

@nocopy
class Tok:
    s: StrView
    n: int32
    def __init__(self, s: str, n: int32) -> None:
        self.s = s
        self.n = n

class Vw:
    s: StrView
    n: int32
    def __init__(self, s: str, n: int32) -> None:
        self.s = s
        self.n = n

def mk(k: int32) -> str:
    return "x" * (4 + k)

def refill_hoist(again: bool) -> int32:
    saved: Cell | None = None
    while True:
        cell = Cell(1)
        if again:
            saved = cell
            again = False
            continue
        break
    alias = cell
    alias.value = 5
    if saved is not None:
        return saved.value + cell.value
    return cell.value

def refill_hoist_ok(again: bool) -> int32:
    while True:
        cell = Cell(1)
        if again:
            again = False
            continue
        break
    alias = cell
    alias.value = 5
    return alias.value + cell.value

def refill_reseat(n: int32) -> int32:
    o: P | None = None
    keep: P | None = None
    i = 0
    while i < n:
        o = P(i)
        if i == 0:
            keep = o
        i += 1
    if keep is not None:
        return keep.x
    return 0

def refill_reseat_ok(n: int32) -> int32:
    o: P | None = None
    i = 0
    total = 0
    while i < n:
        o = P(i)
        if o is not None:
            total += o.x
        i += 1
    return total

def anchored(n: int32, flag: bool) -> int32:
    i = 0
    if flag:
        p = P(0)
    else:
        p = P(1)
    keep = p
    while i < n:
        p = P(i)
        i += 1
    return keep.x + p.x

def escaped() -> int32:
    saved: P = P(0)
    for i in range(3):
        p: P = P(i)
        saved = p
    return saved.x

def hoist_view(k: int32, flag: bool) -> int32:
    a = mk(k)
    if flag:
        t = Tok(a, 1)
    else:
        return 0
    alias = t
    a = mk(k + 1)
    alias.n = 3
    return len(alias.s) + t.n

def hoist_refill(k: int32, flag: bool) -> int32:
    a = mk(k)
    b = mk(k + 2)
    if flag:
        t = Tok(a, 1)
    else:
        return 0
    alias = t
    a = mk(k + 1)
    alias.n = len(b)
    return len(alias.s) + t.n

def hoist_ok(k: int32, flag: bool) -> int32:
    a = mk(k)
    if flag:
        t = Tok(a, 1)
    else:
        return 0
    alias = t
    alias.n = 3
    return len(alias.s) + t.n

def loop_refill(k: int32, flag: bool) -> int32:
    total = 0
    i = 0
    a = mk(1)
    b = mk(2)
    while i < k:
        if flag:
            t = Tok(a, i)
        else:
            t = Tok(b, i)
        alias = t
        alias.n = 2
        total += len(alias.s) + t.n
        i += 1
    return total

def live_member(k: int32) -> int32:
    a = mk(k)
    t = (Tok(a, 1), 42)
    a = mk(k + 1)
    return len(t[0].s)

def dead_member(k: int32) -> int32:
    a = mk(k)
    t = (Tok(a, 1), 42)
    a = mk(k + 1)
    return t[1]

def tuple_copy(k: int32) -> int32:
    a = mk(k)
    t = (Vw(a, 1), 2)
    u = t
    a = mk(k + 1)
    return len(u[0].s)

def tuple_copy_ok(k: int32) -> int32:
    a = mk(k)
    t = (Vw(a, 1), 2)
    u = t
    n = len(u[0].s)
    a = mk(k + 1)
    return n + len(a)

def tuple_move(k: int32) -> int32:
    a = mk(k)
    t = (Tok(a, 1), 2)
    u = t
    a = mk(k + 1)
    return len(u[0].s)

def make_pair(s: str) -> tuple[Own[Vw], int32]:
    return (Vw(s, 1), 2)

def call_pair(k: int32) -> int32:
    a = mk(k)
    u = make_pair(a)
    a = mk(k + 1)
    return len(u[0].s)

S = mk(0)
G = (Vw(S, 1), 2)

def global_pair(k: int32) -> int32:
    global S
    S = mk(k + 1)
    return len(G[0].s)

def global_read() -> int32:
    return len(G[0].s)

def param_pair(t: tuple[Vw, int32], k: int32) -> int32:
    u = t
    a = mk(k)
    return len(u[0].s) + len(a)

def empty_copy(flag: bool, k: int32) -> int32:
    a = mk(k)
    o: Tok | None = None
    if flag:
        o = Tok(a, 1)
    p = o
    if p is not None:
        return len(p.s)
    return 0
'''

Lowered = tuple[dict[str, MIRFunction | str], str, Compiler]


@pytest.fixture(scope="module")
def lowered() -> Lowered:
    """Each body as the call workspace lowers it (its callees' summaries
    known), else its refusal's reason; the generated C++; and the compiler,
    whose registered records a forged body's validation reads."""
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    (_, cpp), _ctx = compiler.generate_code_and_thir(entry)
    ctx = compiler.collect_thir(entry, tolerate_reject=True)
    with compiler.mir_analysis([(entry, ctx)]) as program:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, program.definitions,
                                    compiler.thir_reject_by_node, program.workspace)
    return {v.name: v.function if v.function is not None else v.reason for v in verdicts}, cpp, compiler


def _body(lowered: Lowered, name: str) -> MIRFunction:
    fn = lowered[0][name]
    assert isinstance(fn, MIRFunction), fn
    return fn


def _retention(fn: MIRFunction) -> MIRRetention:
    result = analyze_retention(prepared(fn))
    assert isinstance(result, MIRRetention), result
    return result


def _sums(fn: MIRFunction) -> list[MIRSlot]:
    return [s for s in fn.slots if s.shape.owned_sum]


def _named(fn: MIRFunction, sid: MIRSlotId) -> str | None:
    return next(s.name for s in fn.slots if s.id == sid)


@pytest.mark.parametrize("name,holder", [("refill_hoist", "saved"), ("refill_reseat", "keep")])
def test_a_refill_of_the_same_alternative_replaces_the_retained_record(lowered: Lowered, name: str,
                                                                      holder: str) -> None:
    # A record has identity: refilling the selected alternative replaces the
    # object a holder taken on an earlier pass still points at.
    fn = _body(lowered, name)
    sum_slot, = _sums(fn)
    conflict, = _retention(fn).conflicts
    assert conflict.affected.place == MIRPlace(sum_slot.id, (MIRUnionPayload(1),))
    assert _named(fn, conflict.holder.root) == holder


@pytest.mark.parametrize("name", ["refill_hoist_ok", "refill_reseat_ok"])
def test_a_refill_nothing_retains_is_clean(lowered: Lowered, name: str) -> None:
    assert _retention(_body(lowered, name)).conflicts == ()


def test_an_optional_holder_takes_the_payload_of_its_sites_sum(lowered: Lowered) -> None:
    # `o = P(i)` fills the site's sum; `o` takes `optional_to_ptr` of it: a
    # sum copy into a holder, which raises nothing and lends the payload.
    fn = _body(lowered, "refill_reseat")
    sum_slot, = _sums(fn)
    o = next(s.id for s in fn.slots if s.name == "o")
    copy, = (stmt for b in fn.blocks for stmt in b.statements
             if isinstance(stmt, MIRAssign) and stmt.target == MIRPlace(o) and isinstance(stmt.value, MIRCopy))
    assert copy.value == MIRCopy(MIRPlace(sum_slot.id), False)
    held = {ref for state in prepared(fn).dependencies.referents.values()
            for ref in state.get(MIRPlace(o, (MIRUnionPayload(1),)), ())}
    assert held == {MIRReferent(MIRPlace(sum_slot.id, (MIRUnionPayload(1),)))}


def test_a_rebind_slot_is_built_empty_at_its_anchoring_statement(lowered: Lowered) -> None:
    # C++ declares each rebind slot just before the body-level statement
    # holding its rebind: the `if`'s two before the branch, the loop's one
    # after `keep = p` and before the loop -- never inside it.
    fn, cpp = _body(lowered, "anchored"), lowered[1]
    body = cpp[cpp.index(" anchored("):].split("\n}\n", 1)[0]
    assert re.search(r"std::optional<P> __slot_3;\n\s+while", body)
    sums = _sums(fn)
    assert len(sums) == 3 and all(s.storage_duration is MIRStorageDuration.BODY for s in sums)
    keep = next(s.id for s in fn.slots if s.name == "keep")
    order = [(stmt.target.root, "init") if isinstance(stmt, MIRStorageInit) else (stmt.target.root, "write")
             for b in sorted(fn.blocks, key=lambda b: b.id.index) for stmt in b.statements
             if isinstance(stmt, MIRStorageInit) or isinstance(stmt, MIRAssign) and stmt.target == MIRPlace(keep)]
    assert order == [(sums[0].id, "init"), (sums[1].id, "init"), (keep, "write"), (sums[2].id, "init")]
    inits = {stmt.target.root: b for b in fn.blocks for stmt in b.statements if isinstance(stmt, MIRStorageInit)}
    assert all(block.region == fn.blocks[0].region for block in inits.values())


def test_a_hoisted_pointer_locals_sum_is_built_at_the_body_top(lowered: Lowered) -> None:
    fn, cpp = _body(lowered, "escaped"), lowered[1]
    body = cpp[cpp.index(" escaped("):].split("\n}\n", 1)[0]
    assert body.index("std::optional<P> __slot_") < body.index("P __slot_")
    sum_slot, = _sums(fn)
    assert isinstance(fn.blocks[0].statements[0], MIRStorageInit)
    assert fn.blocks[0].statements[0].target == MIRPlace(sum_slot.id)


def test_a_view_holding_record_in_a_sum_keys_its_loans_under_the_payload(lowered: Lowered) -> None:
    # `a` rewritten while `alias` still reads the view the hoisted record stores.
    fn = _body(lowered, "hoist_view")
    sum_slot, = _sums(fn)
    member = next(f for f in fn.records if f.type.name == "Tok").fields[0]
    key = MIRPlace(sum_slot.id, (MIRUnionPayload(1), member))
    assert key in prepared(fn).dependencies.stored_loans
    a = next(s.id for s in fn.slots if s.name == "a")
    conflict, = _retention(fn).conflicts
    assert conflict.holder == key and conflict.affected.place.root == a
    assert _retention(_body(lowered, "hoist_ok")).conflicts == ()


def _refilled(fn: MIRFunction, *, empty_first: bool) -> tuple[MIRFunction, MIRPlace, MIRBlockId, int]:
    """`hoist_refill` with its one sum refilled in place right after the
    fill from `a`: from `b`, optionally emptied first. A loop refills its
    site's one slot every pass, but the loop-head join accumulates the
    operand's referents (a view rebound from `a` to `b` holds both at the
    fill), so strong and weak replacement look alike at the fixpoint; the
    refill is spliced in on one straight path instead: a new view of `b`
    and a new built record. Returns the
    body, the payload's view key, and the fill block and the index of the
    first spliced statement."""
    sum_slot, = _sums(fn)
    member = next(f for f in fn.records if f.type.name == "Tok").fields[0]
    key = MIRPlace(sum_slot.id, (MIRUnionPayload(1), member))
    block = next(b for b in fn.blocks if any(isinstance(s, MIRAssign) and isinstance(s.value, MIRWrap)
                                             for s in b.statements))
    view, count, construct, wrap, borrow = block.statements
    assert isinstance(construct, MIRAssign) and isinstance(construct.value, MIRConstruct)
    b = next(s for s in fn.slots if s.name == "b")
    next_id = max(s.id.index for s in fn.slots) + 1
    view2 = replace(next(s for s in fn.slots if s.id == view.target.root), id=MIRSlotId(fn.id, next_id))
    built2 = replace(next(s for s in fn.slots if s.id == construct.target.root), id=MIRSlotId(fn.id, next_id + 1))
    refill = (replace(view, target=MIRPlace(view2.id), value=MIRBorrow(MIRPlace(b.id))),
              replace(construct, target=MIRPlace(built2.id), value=MIRConstruct((view2.id, count.target.root))),
              replace(wrap, value=MIRWrap(1, MIRPlace(built2.id))),
              borrow)
    emptied = (replace(wrap, value=MIRWrap(0)),) if empty_first else ()
    spliced = replace(block, statements=(*block.statements, *emptied, *refill))
    blocks = tuple(spliced if blk.id == block.id else blk for blk in fn.blocks)
    return replace(fn, slots=(*fn.slots, view2, built2), blocks=blocks), key, block.id, len(block.statements)


def test_a_refill_of_one_sum_strongly_replaces_the_payloads_stored_loans(lowered: Lowered) -> None:
    # One slot filled from `a`, then refilled from `b`: the key holds `b`'s
    # loan alone, so rewriting `a` afterwards no longer conflicts.
    original = _body(lowered, "hoist_refill")
    names = {s.name: s.id for s in original.slots if s.name is not None}
    assert {c.affected.place.root for c in _retention(original).conflicts} == {names["a"]}
    fn, key, block, first = _refilled(original, empty_first=False)
    deps = prepared(fn).dependencies
    assert deps.referents[MIRPoint(block, first)][key] == {MIRReferent(MIRPlace(names["a"]))}
    assert deps.referents[MIRPoint(block, first + 3)][key] == {MIRReferent(MIRPlace(names["b"]))}
    assert _retention(fn).conflicts == ()


def test_an_empty_refill_clears_the_payloads_stored_loans(lowered: Lowered) -> None:
    # A write selecting the alternative without storage ends the payload's
    # entries; the next fill starts from nothing.
    fn, key, block, first = _refilled(_body(lowered, "hoist_refill"), empty_first=True)
    names = {s.name: s.id for s in fn.slots if s.name is not None}
    deps = prepared(fn).dependencies
    assert key in deps.referents[MIRPoint(block, first)]
    assert key not in deps.referents[MIRPoint(block, first + 1)]
    assert deps.referents[MIRPoint(block, first + 4)][key] == {MIRReferent(MIRPlace(names["b"]))}
    assert _retention(fn).conflicts == ()


def test_each_site_slot_stores_the_loan_of_its_own_fill(lowered: Lowered) -> None:
    # Two sites, two slots (from `a` and from `b`): each slot's key holds its
    # own fill's loan after the fill, never the other site's.
    fn = _body(lowered, "loop_refill")
    member = next(f for f in fn.records if f.type.name == "Tok").fields[0]
    deps = prepared(fn).dependencies
    names = {s.name: s.id for s in fn.slots if s.name is not None}
    for sum_slot, source in zip(_sums(fn), ("a", "b")):
        key = MIRPlace(sum_slot.id, (MIRUnionPayload(1), member))
        fill = next((b.id, i) for b in fn.blocks for i, stmt in enumerate(b.statements)
                    if isinstance(stmt, MIRAssign) and stmt.target == MIRPlace(sum_slot.id)
                    and isinstance(stmt.value, MIRWrap))
        after = deps.referents[MIRPoint(fill[0], fill[1] + 1)]
        assert after[key] == {MIRReferent(MIRPlace(names[source]))}
    assert _retention(fn).conflicts == ()


def test_an_owned_tuple_member_keys_its_loans_under_its_index(lowered: Lowered) -> None:
    # `t[0].s` is read after `a` is rewritten: a real conflict on the member's key.
    live = _body(lowered, "live_member")
    t = next(s for s in live.slots if s.name == "t")
    member = next(f for f in live.records if f.type.name == "Tok").fields[0]
    key = MIRPlace(t.id, (MIRTupleIndex(0), member))
    assert key in object_keys(t, live.record_layouts)
    conflict, = _retention(live).conflicts
    assert conflict.holder == key
    # A false conflict: liveness is per slot root. `t` stays live for
    # `t[1]`, so the dead `t[0].s` still conflicts.
    dead = _body(lowered, "dead_member")
    assert {c.holder for c in _retention(dead).conflicts} == {
        MIRPlace(next(s.id for s in dead.slots if s.name == "t"), (MIRTupleIndex(0), member))}


def test_a_pointer_member_holding_a_view_stays_refused(lowered: Lowered) -> None:
    # A `Tok | None` local points at its payload: its loans lie behind a
    # pointer, which no stored-loan key reaches.
    assert lowered[0]["empty_copy"] == "wrapper holds a borrow"


@pytest.mark.parametrize("name", ["tuple_copy", "tuple_move"])
def test_a_whole_tuple_rebinding_names_the_same_object(lowered: Lowered, name: str) -> None:
    # `u = t` of an owned tuple local binds the same object (C++ `auto&& u =
    # t`), so `u[0].s` reads `t`'s key and rewriting `a` conflicts.
    fn = _body(lowered, name)
    t, = (s for s in fn.slots if s.shape.owned_tuple)
    a = next(s.id for s in fn.slots if s.name == "a")
    member = next(f for f in fn.records if f.type == t.shape.members[0].type).fields[0]
    conflict, = _retention(fn).conflicts
    assert conflict.holder == MIRPlace(t.id, (MIRTupleIndex(0), member)) and conflict.affected.place.root == a
    assert _retention(_body(lowered, "tuple_copy_ok")).conflicts == ()


def test_an_owned_tuple_is_written_only_by_its_construction(lowered: Lowered) -> None:
    # A whole copy into an owned tuple would carry no member loans
    # (`stored_on_fill` fills a tuple only from a construct): the validator
    # admits only the construction.
    fn = _body(lowered, "tuple_copy")
    t, = (s for s in fn.slots if s.shape.owned_tuple)
    u = replace(t, id=MIRSlotId(fn.id, max(s.id.index for s in fn.slots) + 1), name="u")
    block = next(b for b in fn.blocks if any(isinstance(s, MIRAssign) and s.target == MIRPlace(t.id)
                                             for s in b.statements))
    built = next(s for s in block.statements if isinstance(s, MIRAssign) and s.target == MIRPlace(t.id))
    copy = replace(built, target=MIRPlace(u.id), value=MIRCopy(MIRPlace(t.id)))
    forged = replace(fn, slots=(*fn.slots, u), blocks=tuple(
        replace(b, statements=(*b.statements, copy)) if b.id == block.id else b for b in fn.blocks))
    with pytest.raises(MIRValidationError, match="tuple initialization needs owning tuple construction"):
        validate_function(forged)


@pytest.mark.parametrize("name,reason", [
    # A tuple result holding a record by value: no return layout fact.
    ("make_pair", "unsupported return type"),
    ("call_pair", "missing or invalid layout fact"),
    # A global tuple's member is no local payload, and a str global is not written.
    ("global_read", "tuple needs local payload"),
    ("global_pair", "owned-leaf global write"),
    # A tuple parameter points at its record: its loans lie behind a pointer.
    ("param_pair", "wrapper holds a borrow"),
])
def test_a_view_holding_tuple_source_outside_a_producer_is_refused(lowered: Lowered, name: str,
                                                                   reason: str) -> None:
    # The source shapes stay refused end to end, whichever check fires first;
    # the forged slot kinds below pin that the admission alone refuses them.
    assert lowered[0][name] == reason


@pytest.mark.parametrize("kind,placed", [
    # A parameter, a global, a temporary (a call result's) and a local
    # without placed storage: none is a producer's slot.
    (MIRSlotKind.PARAMETER, True),
    (MIRSlotKind.GLOBAL, False),
    (MIRSlotKind.TEMPORARY, True),
    (MIRSlotKind.LOCAL, False),
])
def test_a_value_member_outside_a_producer_slot_stays_refused(lowered: Lowered, kind: MIRSlotKind,
                                                              placed: bool) -> None:
    # "Wrapper holds a borrow" lifts only in a producer's slot, the one whose
    # inline records' stored loans the body keys: `live_member`'s owned tuple
    # local validates; the same slot as any other kind is refused by the
    # admission rule, before any later slot check.
    fn = _body(lowered, "live_member")
    t = next(s for s in fn.slots if s.name == "t")
    assert t.producer and t.shape.owned_tuple
    # A parameter and a global reside in no region of the body.
    local = kind in (MIRSlotKind.LOCAL, MIRSlotKind.TEMPORARY)
    moved = replace(t, kind=kind, storage_duration=t.storage_duration if placed else None,
                    residence=t.residence if local else None)
    assert not moved.producer
    forged = replace(fn, slots=tuple(moved if s.id == t.id else s for s in fn.slots))
    with activate_compiler(lowered[2]):
        validate_function(fn)
        assert shape_problem(moved.shape, moved.type, producer=moved.producer) == "wrapper holds a borrow"
        if kind is MIRSlotKind.PARAMETER:
            # The validator first demands a parameter's binding, which the
            # forged body lacks: the admission is checked on the slot above.
            return
        with pytest.raises(MIRValidationError, match="^slot wrapper holds a borrow$"):
            validate_function(forged)


def test_the_borrow_lift_is_the_producer_admission() -> None:
    # The shape check alone: a tuple or an Optional storing a view-holding
    # record inline is admitted only in a producer's slot.
    tok = hand_record("Tok", "sums.Tok", s=STRVIEW, n=INT32)
    pair = TupleType((tok, INT32))
    owned_pair = tuple_shape(pair, (owned_shape(tok), leaf_shape(INT32)))
    owned_sum = optional_shape(OptionalType(tok), owned_shape(tok))
    for shape in (owned_pair, owned_sum):
        assert shape_problem(shape, shape.type) == "wrapper holds a borrow"
        assert shape_problem(shape, shape.type, producer=True) is None


def test_a_sum_fill_rebases_a_wrap_and_copies_only_held_loans() -> None:
    # The fill rule on its own: a wrap carries its operand's stored loans to
    # the payload's keys; a copy from a sum that holds none stores none and
    # raises nothing; a wrap of the empty alternative stores nothing.
    body = MIRBodyId("sums", "fill")
    tok = hand_record("Tok", "sums.Tok", s=STRVIEW, n=INT32)
    view = MIRField(MIRFieldId(tok, "s"), STRVIEW)
    layouts = records_of(MIRRecordLayout(tok, (view, MIRField(MIRFieldId(tok, "n"), INT32)), True, True))
    target, built, source, buffer = (MIRSlotId(body, i) for i in range(4))
    sum_shape = optional_shape(OptionalType(tok), owned_shape(tok))
    slots = {target: MIRSlot(target, sum_shape, MIRSlotKind.LOCAL, storage_duration=MIRStorageDuration.BODY),
             built: MIRSlot(built, owned_shape(tok), MIRSlotKind.TEMPORARY, storage_duration=MIRStorageDuration.BODY),
             source: MIRSlot(source, sum_shape, MIRSlotKind.LOCAL, storage_duration=MIRStorageDuration.BODY),
             buffer: MIRSlot(buffer, leaf_shape(INT32), MIRSlotKind.LOCAL)}
    objects = {sid: object_keys(slot, layouts) for sid, slot in slots.items()}
    key = MIRPlace(target, (MIRUnionPayload(1), view))
    assert objects[target] == (key,)
    loan = frozenset({MIRReferent(MIRPlace(buffer))})
    state = {MIRPlace(built, (view,)): loan}
    assert stored_on_fill(target, MIRWrap(1, MIRPlace(built)), state, slots, layouts, objects) == {key: loan}
    assert stored_on_fill(target, MIRCopy(MIRPlace(source)), state, slots, layouts, objects) == {}
    assert stored_on_fill(target, MIRWrap(0), state, slots, layouts, objects) == {}
    held = {MIRPlace(source, (MIRUnionPayload(1), view)): loan}
    assert stored_on_fill(target, MIRCopy(MIRPlace(source)), held, slots, layouts, objects) == {key: loan}
