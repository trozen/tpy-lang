"""MIR over records with view members: the member initializer stores a lent
loan (BORROW), the dependency state keys a record object's stored loans at
its member places -- set by the write that fills the body's own storage,
seeded opaque (`held`) for a borrowed parameter -- a view member read
resolves through its holder to them, one closure decides which entries are
live, and every way such a record could move refuses with its own reason."""

from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing
from ..typesys import Loan, NominalType, OptionalType, OwnType, TupleType, UnionType, loan_class
from .call_contract import MIRParameterWrite, MIRReturnOrigin, MIRSummaryState, summary_problem
from .collect import MIRBodyVerdict, enumerate_bodies
from .coverage import MIRUnsupported
from .definitions import MIRDefinitions, MIRHeldLayout, constructor_initialization
from .dependencies import (
    MIRReferent, MIRUnseededLoan, analyze_dependencies, dump_dependencies, live_holders, object_keys,
    resolve_referents, stored_on_fill,
)
from .liveness import MIRPoint, analyze_liveness
from .nodes import (
    MIRAssign, MIRBorrow, MIRConstant, MIRConstruct, MIRCopy, MIRDeref, MIRField, MIRFunction,
    MIRMemberInitMode, MIRMove, MIRNotCovered, MIROptionalLayout, MIRPlace, MIRRecordWrite, MIRRecordWriteMode,
    MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration, MIRTupleElement, MIRTupleLayout,
    MIRUnionLayout, MIRValueKind,
)
from .retention import affects, may_overlap
from .validate import MIRValidationError, validate_function

SOURCE = """\
from tpy import Own, StrView, int32, copy


class Tok:
    s: StrView
    n: int32

    def __init__(self, s: str, n: int32) -> None:
        self.s = s
        self.n = n

    def text(self) -> StrView:
        return self.s


class Viewed:
    v: StrView

    def __init__(self, v: StrView) -> None:
        self.v = v


class Lit:
    s: StrView

    def __init__(self) -> None:
        self.s = "lit"


class Owning:
    s: StrView

    def __init__(self, s: Own[str]) -> None:
        self.s = s


class Base:
    s: StrView

    def __init__(self, s: str) -> None:
        self.s = s


class Derived(Base):
    k: int32

    def __init__(self, s: str, k: int32) -> None:
        super().__init__(s)
        self.k = k


class Child(Tok):
    pass


class Holder:
    tok: Tok

    def __init__(self, tok: Tok) -> None:
        self.tok = copy(tok)


class Reader:
    data: StrView
    size: int32

    def __init__(self, data: str) -> None:
        self.data = data
        self.size = len(data)

    def head(self) -> int32:
        return len(self.data)


def mk(k: int32) -> str:
    return "x" * (4 + k)


def size(t: Tok) -> int32:
    return len(t.s)


def first(t: Tok) -> StrView:
    return t.s


def lent_member(h: Holder) -> int32:
    return size(h.tok)


def built(k: int32) -> int32:
    buf = mk(k)
    t = Tok(buf, 1)
    u = copy(t)
    w = u
    return len(w.s) + len(t.s)


def seeded(t: Tok) -> int32:
    return len(t.s)


def literal() -> int32:
    c = Lit()
    return len(c.s)


class Tree:
    s: StrView
    kids: list[Tree]

    def __init__(self, s: str) -> None:
        self.s = s
        self.kids = []


def opt(t: Tree | None) -> int32:
    if t is not None:
        return len(t.s)
    return 0


class Forest:
    first: Tree

    def __init__(self, first: Tree) -> None:
        self.first = copy(first)


class Raw:
    s: StrView
    data: bytearray

    def __init__(self, s: str) -> None:
        self.s = s
        self.data = bytearray()


def raw(r: Raw) -> int32:
    return len(r.s)
"""


@dataclass(frozen=True)
class _Program:
    compiler: object
    constructors: dict[str, th.THIRConstructor]
    definitions: MIRDefinitions
    verdicts: dict[str, MIRBodyVerdict]
    # The compilation's record TypeDefs, which the per-test state reset
    # clears and `holds_loan` reads.
    type_defs: dict


@pytest.fixture(scope="module")
def program():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                    compiler.thir_reject_by_node, mir.workspace)
        yield _Program(compiler, {c.record_name: c for c in ctx.thir_constructors.values()}, mir.definitions,
                       {v.body.declaration.split("@")[0]: v for v in verdicts}, dict(compiler.dynamic_type_defs))


@pytest.fixture(autouse=True)
def active(program):
    program.compiler.dynamic_type_defs.update(program.type_defs)
    with activate_compiler(program.compiler):
        yield


def _definition(program: _Program, name: str):
    return next(d for t, d in program.definitions.records.items() if t.name == name)


def _record(program: _Program, name: str) -> NominalType:
    return program.constructors[name].record_layout.type


def _lowered(program: _Program, name: str) -> MIRFunction:
    fn = program.verdicts[name].function
    assert isinstance(fn, MIRFunction), program.verdicts[name].reason
    return fn


def _member(program: _Program, record: str, name: str) -> MIRField:
    layout = program.definitions.layouts[_record(program, record)]
    return next(f for f in layout.fields if f.id.name == name)


def _assigns(fn: MIRFunction) -> list[MIRAssign]:
    return [s for b in fn.blocks for s in b.statements if isinstance(s, MIRAssign)]


def _with_statement(fn: MIRFunction, stmt: MIRAssign, **changes) -> MIRFunction:
    return replace(fn, blocks=tuple(replace(b, statements=tuple(replace(s, **changes) if s is stmt else s
                                                                 for s in b.statements)) for b in fn.blocks))


def _with_slot(fn: MIRFunction, sid: MIRSlotId, **changes) -> MIRFunction:
    return replace(fn, slots=tuple(replace(s, **changes) if s.id == sid else s for s in fn.slots))


# --- definitions ------------------------------------------------------------------------

def test_a_view_member_stores_a_lent_loan(program: _Program) -> None:
    # A str parameter the caller lends, a view parameter's loan, a literal's static storage.
    tok = program.definitions.records[_record(program, "Tok")]
    assert [(i.field.id.name, i.source, i.mode, i.may_raise) for i in tok.initializers] == [
        ("s", "s", MIRMemberInitMode.BORROW, False), ("n", "n", MIRMemberInitMode.SCALAR, False)]
    viewed = program.definitions.records[_record(program, "Viewed")]
    assert [(i.source, i.mode) for i in viewed.initializers] == [("v", MIRMemberInitMode.BORROW)]
    lit = program.definitions.records[_record(program, "Lit")]
    assert [(i.source, i.mode) for i in lit.initializers] == [(MIRConstant("lit"), MIRMemberInitMode.BORROW)]
    # The receiver's entry initialization stores the loan, no copy and no exit.
    ctor = _lowered(program, "Tok.__init__")
    assert [m.mode for m in ctor.receiver_init.fields] == [MIRMemberInitMode.BORROW, MIRMemberInitMode.SCALAR]
    assert not ctor.exceptional_exits


@pytest.mark.parametrize("name,reason", [
    # The constructor's own copy dies at return: no record may keep its loan.
    ("Owning", "constructor view needs a lent parameter"),
    # A base or inherited constructor's stored loan would be the leg's, unmodeled.
    ("Derived", "base argument borrow not modeled"),
    ("Child", "inherited constructor borrow"),
    # A member record's stored loans would be keyed under the record around it.
    ("Holder", "record member holds a borrow"),
])
def test_unmodeled_stored_loans_refuse(program: _Program, name: str, reason: str) -> None:
    assert _definition(program, name) == reason


def test_a_recursive_record_holding_a_view_holds_a_borrow(program: _Program) -> None:
    # `kids` re-enters Tree; the frame that entered Tree sees its view member,
    # so the recursion is decided: Tree, and a record holding one, hold a borrow.
    with activate_compiler(program.compiler):
        assert loan_class(_record(program, "Tree")).holds is Loan.YES
    assert _definition(program, "Tree") == "record member holds a borrow"
    assert _definition(program, "Forest") == "record member holds a borrow"
    assert program.verdicts["opt"].reason == "wrapper holds a borrow"


def test_a_member_of_unknown_loan_class_beside_a_view_member_refuses(program: _Program) -> None:
    # A bytearray's loan class is undecided: beside a view member it could
    # hold a loan no entry keys, so the record has neither definition nor layout.
    reason = "record member loan unknown beside a view member"
    assert _definition(program, "Raw") == reason
    assert _record(program, "Raw") not in program.definitions.layouts
    assert program.verdicts["raw"].reason == reason


def test_a_constructor_view_conversion_keeps_the_view(program: _Program) -> None:
    ctor = program.constructors["Tok"]
    mil = ctor.mil_inits[0]
    assert isinstance(mil.value, th.THIRCoerce)
    stored = replace(ctor, mil_inits=(replace(mil, value=replace(mil.value, form=th.Form.STORAGE)),
                                      *ctor.mil_inits[1:]))
    with pytest.raises(MIRUnsupported) as refused:
        constructor_initialization(stored)
    assert refused.value.reason == "constructor view conversion"


def test_a_record_without_a_definition_has_a_held_layout(program: _Program) -> None:
    reader = _record(program, "Reader")
    # A call initializes `size`: no caller constructs through it, but a
    # holder's layout is decided from the fields alone.
    assert program.definitions.records[reader] == "constructor initializer needs parameter or literal"
    assert isinstance(program.definitions.held_layout(None, reader), MIRHeldLayout)
    assert program.definitions.layouts[reader].fields[0].id.name == "data"
    head = _lowered(program, "Reader.head")
    assert reader in {r.type for r in head.records} and not program.verdicts["Reader.head"].conflicts
    # A member record holding a loan has no layout either.
    assert _record(program, "Holder") not in program.definitions.layouts


def test_a_lent_member_holding_a_view_refuses(program: _Program) -> None:
    # Its stored loans would be keyed under the record around it, which no entry seeds.
    assert program.verdicts["lent_member"].reason == "lent member holds a borrow"


# --- the dependency state ------------------------------------------------------------------

def _state_at_return(fn: MIRFunction):
    dependencies = analyze_dependencies(fn, analyze_liveness(fn))
    assert not isinstance(dependencies, MIRNotCovered), dependencies
    block = next(b for b in fn.blocks if isinstance(b.terminator, MIRReturn))
    return dependencies, dependencies.referents[MIRPoint(block.id, len(block.statements))]


def test_stored_loans_follow_the_object(program: _Program) -> None:
    fn = _lowered(program, "built")
    member = _member(program, "Tok", "s")
    _, state = _state_at_return(fn)
    buf = next(s.id for s in fn.slots if s.name == "buf")
    storage = [s.id for s in fn.slots if s.value_kind is MIRValueKind.OWNED and s.type == _record(program, "Tok")]
    # The construct's object, its copy and the copy moved on all store the buffer's loan.
    assert len(storage) == 3
    for sid in storage:
        assert state[MIRPlace(sid, (member,))] == frozenset({MIRReferent(MIRPlace(buf))})
    writes = {type(s.value) for s in _assigns(fn) if not s.target.projections and s.target.root in storage}
    assert writes == {MIRConstruct, MIRCopy, MIRMove}


def test_a_fill_with_no_stored_loan_to_carry_refuses(program: _Program) -> None:
    fn = _lowered(program, "built")
    slots = {s.id: s for s in fn.slots}
    layouts = {r.type: r for r in fn.records}
    objects = {sid: object_keys(slot, layouts) for sid, slot in slots.items()}
    tok = _record(program, "Tok")
    source, target = [s.id for s in fn.slots if s.value_kind is MIRValueKind.OWNED and s.type == tok][:2]
    # A move carries the source object's entry; with none recorded it
    # refuses rather than storing an empty set.
    with pytest.raises(MIRUnseededLoan) as moved:
        stored_on_fill(target, MIRMove(source), {}, slots, layouts, objects)
    assert moved.value.reason == "record move of an object with no stored loan"
    # A fill no fact names (a call's result) never stores nothing.
    with pytest.raises(MIRUnseededLoan) as filled:
        stored_on_fill(target, MIRConstant(1), {}, slots, layouts, objects)
    assert filled.value.reason == "record storage filled with no stored loan"


def test_a_parameter_s_stored_loans_are_seeded_held(program: _Program) -> None:
    fn = _lowered(program, "seeded")
    member = _member(program, "Tok", "s")
    dependencies, state = _state_at_return(fn)
    key = MIRPlace(fn.slots[0].id, (member,))
    held = MIRReferent(key, external=True, held=True)
    assert dependencies.entry_active[key] == frozenset({held})
    # The view read resolves through the holder to the seeded entry.
    assert resolve_referents(MIRPlace(fn.slots[0].id, (MIRDeref(), member)), state, {s.id: s for s in fn.slots}) == {
        held}
    assert "held:%0.__main__.Tok::s" in dump_dependencies(dependencies)


def test_a_literal_member_stores_static_storage(program: _Program) -> None:
    fn = _lowered(program, "literal")
    member = _member(program, "Lit", "s")
    _, state = _state_at_return(fn)
    storage = next(s.id for s in fn.slots if s.value_kind is MIRValueKind.OWNED)
    loans = state[MIRPlace(storage, (member,))]
    assert len(loans) == 1 and next(iter(loans)).static


def test_an_object_with_no_stored_loan_entry_refuses(program: _Program) -> None:
    fn = _lowered(program, "seeded")
    tok = _record(program, "Tok")
    # With no layout of the record, no entry is seeded: the read refuses,
    # never an empty origin set.
    bare = replace(fn, records=tuple(r for r in fn.records if r.type != tok))
    validate_function(bare)
    result = analyze_dependencies(bare, analyze_liveness(bare))
    assert isinstance(result, MIRNotCovered) and result.reason == "view member read of an object with no stored loan"
    param = fn.slots[0].id
    with pytest.raises(MIRUnseededLoan):
        resolve_referents(MIRPlace(param, (MIRDeref(), _member(program, "Tok", "s"))),
                          {MIRPlace(param): frozenset({MIRReferent(MIRPlace(param), external=True)})},
                          {s.id: s for s in fn.slots})


def test_live_holders_reach_stored_loans_through_their_objects(program: _Program) -> None:
    fn = _lowered(program, "built")
    member = _member(program, "Tok", "s")
    holder, storage, other, buf = (MIRSlotId(fn.id, i) for i in (100, 101, 102, 103))
    key, unreached = MIRPlace(storage, (member,)), MIRPlace(other, (member,))
    state = {MIRPlace(holder): frozenset({MIRReferent(MIRPlace(storage))}),
             key: frozenset({MIRReferent(MIRPlace(buf))}),
             unreached: frozenset({MIRReferent(MIRPlace(buf))})}
    # The holder is live, its object's stored loan with it; another object's is not.
    stored = frozenset({key, unreached})
    assert live_holders(state, frozenset({holder}), stored) == {MIRPlace(holder), key}
    # A stored loan never keeps its object's holder alive.
    assert live_holders(state, frozenset({storage}), stored) == {key}
    assert live_holders(state, frozenset(), stored) == frozenset()
    # An entry no object stores is a holder leaf, live only with its slot.
    assert live_holders(state, frozenset({holder}), frozenset()) == {MIRPlace(holder)}


def test_a_held_loan_may_be_any_external_storage(program: _Program) -> None:
    fn = _lowered(program, "seeded")
    slots = {s.id: s for s in fn.slots}
    member = _member(program, "Tok", "s")
    p, q = MIRSlotId(fn.id, 50), MIRSlotId(fn.id, 51)
    held = MIRReferent(MIRPlace(p, (member,)), external=True, held=True)
    sibling = MIRReferent(MIRPlace(p, (_member(program, "Tok", "n"),)), external=True)
    other = MIRReferent(MIRPlace(q), external=True)
    # A sibling field of the very object, or another external object: the
    # held rule runs before origins and paths are compared.
    assert affects(sibling, held, slots) and affects(other, held, slots)
    assert may_overlap(held, sibling) and may_overlap(other, held)
    # Private and static storage are never what an external loan views.
    assert not affects(MIRReferent(MIRPlace(q)), held, slots)
    assert not affects(MIRReferent(MIRPlace(q), external=True, static=True), held, slots)
    assert not may_overlap(MIRReferent(MIRPlace(q)), held)


# --- summaries ---------------------------------------------------------------------------------

def test_a_view_result_off_a_member_keeps_its_path(program: _Program) -> None:
    summary = program.verdicts["first"].summary
    assert summary.state is MIRSummaryState.KNOWN
    member = _member(program, "Tok", "s")
    path = (th.THIRFieldIdentity(member.id.owner, member.id.name, member.type),)
    assert summary.summary.returns == frozenset({MIRReturnOrigin(0, path)})
    # A view member is a return endpoint, never a write endpoint.
    writing = replace(summary.summary, writes=frozenset({MIRParameterWrite(0, path)}))
    assert summary_problem(writing) == "unsupported call write field or access"


# --- the validator --------------------------------------------------------------------------

def test_a_view_member_place_is_read_whole(program: _Program) -> None:
    fn = _lowered(program, "built")
    holder = next(s.id for s in fn.slots if s.name == "t")
    read = next(s for s in _assigns(fn) if isinstance(s.value, MIRBorrow) and s.value.source.root == holder
                and s.value.source.projections)
    place = read.value.source
    # Only the constructor's member initialization stores a loan.
    with pytest.raises(MIRValidationError, match="view member replacement is unsupported"):
        validate_function(_with_statement(fn, read, target=place, value=MIRCopy(place)))
    # Nothing projects through the loan.
    with pytest.raises(MIRValidationError, match="projection through a view member"):
        validate_function(_with_statement(fn, read, value=MIRBorrow(MIRPlace(place.root, (
            *place.projections, MIRDeref())))))


def test_a_construct_stores_a_view_operand(program: _Program) -> None:
    fn = _lowered(program, "built")
    construct = next(s for s in _assigns(fn) if isinstance(s.value, MIRConstruct))
    count = next(f for f in construct.value.fields if fn.slots[f.index].value_kind is MIRValueKind.SCALAR)
    # A view member takes a lending holder, never a scalar.
    with pytest.raises(MIRValidationError, match="incomplete or mistyped record construction"):
        validate_function(_with_statement(fn, construct, value=replace(construct.value, fields=(
            count, count))))


def test_receiver_view_member_validation(program: _Program) -> None:
    ctor = _lowered(program, "Tok.__init__")
    init = ctor.receiver_init

    def member(**changes) -> MIRFunction:
        return replace(ctor, receiver_init=replace(init, fields=(replace(init.fields[0], **changes),
                                                                 *init.fields[1:])))
    with pytest.raises(MIRValidationError, match="invalid receiver initializer mode"):
        validate_function(member(mode=MIRMemberInitMode.COPY))
    with pytest.raises(MIRValidationError, match="invalid receiver initializer mode"):
        validate_function(member(may_raise=True))
    # A scalar parameter lends nothing; a constant is a literal of the view's family.
    with pytest.raises(MIRValidationError, match="invalid receiver initializer parameter"):
        validate_function(member(source=init.fields[1].source))
    with pytest.raises(MIRValidationError, match="invalid receiver initializer constant"):
        validate_function(member(source=MIRConstant(1)))
    validate_function(member(source=MIRConstant("lit")))


def test_storage_that_would_move_a_stored_loan_refuses(program: _Program) -> None:
    fn = _lowered(program, "seeded")
    tok = _record(program, "Tok")
    param = fn.slots[0].id
    # A wrapper payload or member: its selection would be needed to reach the object.
    for changes in (
            dict(type=OptionalType(tok), value_kind=MIRValueKind.OPTIONAL, form=th.Form.VALUE, readonly=False,
                 optional_layout=MIROptionalLayout(tok, MIRValueKind.BORROWED, True)),
            dict(type=UnionType((tok, _record(program, "Lit"))), value_kind=MIRValueKind.UNION,
                 form=th.Form.VALUE, readonly=False, union_layout=MIRUnionLayout((
                     MIRTupleElement(tok, MIRValueKind.BORROWED, True),
                     MIRTupleElement(_record(program, "Lit"), MIRValueKind.BORROWED, True)))),
            dict(type=TupleType((tok,)), value_kind=MIRValueKind.TUPLE, form=th.Form.VALUE, readonly=False,
                 tuple_layout=MIRTupleLayout((MIRTupleElement(tok, MIRValueKind.BORROWED, True),)))):
        with pytest.raises(MIRValidationError, match="wrapper holds a borrow"):
            validate_function(_with_slot(fn, param, **changes))
    # Handed over at OWN: the body's own storage, which a caller never lends a loan into.
    with pytest.raises(MIRValidationError, match="owned parameter holds a borrow"):
        validate_function(_with_slot(fn, param, value_kind=MIRValueKind.OWNED, form=th.Form.STORAGE,
                                     readonly=False, passing=ParamPassing.OWN,
                                     storage_duration=MIRStorageDuration.BODY))
    # A record member holding a loan: its stored loans would be keyed under the outer record.
    holder = _record(program, "Holder")
    outer = replace(fn.records[0], type=holder, fields=(MIRField(replace(fn.records[0].fields[0].id, owner=holder,
                                                                          name="tok"), tok),), ancestors=())
    with pytest.raises(MIRValidationError, match="record member holds a borrow"):
        validate_function(replace(fn, records=(*fn.records, outer)))


def test_whole_writes_that_would_move_a_stored_loan_refuse(program: _Program) -> None:
    fn = _lowered(program, "built")
    tok = _record(program, "Tok")
    construct = next(s for s in _assigns(fn) if isinstance(s.value, MIRConstruct))
    holder = next(s.id for s in fn.slots if s.name == "t")
    # An in-place reseat through a holder that may reach several objects.
    with pytest.raises(MIRValidationError, match="in-place replacement holds a borrow"):
        validate_function(_with_statement(fn, construct, target=MIRPlace(holder, (MIRDeref(),)),
                                          storage_write=MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, holder)))
    # A result by value: its stored loans would leave for the caller.
    storage = construct.target.root
    block = next(b for b in fn.blocks if isinstance(b.terminator, MIRReturn))
    returned = replace(fn, return_type=OwnType(tok), blocks=tuple(
        replace(b, terminator=MIRReturn(storage)) if b is block else b for b in fn.blocks))
    with pytest.raises(MIRValidationError, match="owned result holds a borrow"):
        validate_function(returned)

