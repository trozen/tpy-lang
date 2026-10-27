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
from ..typesys import Loan, NominalType, OptionalType, OwnType, TupleType, UnionType, loan_class
from .call_contract import MIRParameterWrite, MIRReturnOrigin, MIRSummaryState, summary_problem
from .collect import MIRBodyVerdict, enumerate_bodies
from .definitions import MIRDefinitions, MIRHeldLayout
from .dependencies import (
    MIRReferent, MIRUnseededLoan, dump_dependencies, live_holders, object_keys, resolve_referents, stored_on_fill,
)
from .liveness import MIRPoint
from .testutil import lower_constructor
from .dump import dump_function
from .nodes import (
    MIRAssign, MIRBodyId, MIRBorrow, MIRCall, MIRConstant, MIRCopy, MIRAlias, MIRFunction, MIRMove, MIRNotCovered,
    MIRPlace, MIRRead, MIRReturn, MIRSlotId,
)
from .shapes import MIRDeref
from .value import MIRField, MIRValidationError
from .test_constructors import field_write, rewritten
from .testutil import no_layouts, borrowed_shape, optional_shape, tuple_shape, union_shape, prepared
from .prepare import validate_function, validated
from .validate import MIRPresenceError
from .reach import reaches, storage_exclusions

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


class ViewLent(Viewed):
    def __init__(self, s: str) -> None:
        super().__init__(s)


class ViewLit(Viewed):
    def __init__(self) -> None:
        super().__init__("abc")

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
        result = _Program(compiler, {c.record_name: c for c in ctx.thir_constructors.values()}, mir.definitions,
                       {v.body.declaration.split("@")[0]: v for v in verdicts}, dict(compiler.dynamic_type_defs))
    return result


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

def _stores(program: _Program, name: str) -> list:
    """The loans constructor `name`'s summary stores into its result, by member."""
    summary = program.verdicts[f"{name}.__init__"].summary.summary
    return sorted((t.path[-1].name, t.source) for t in summary.transfers)


def test_a_view_member_stores_a_lent_loan(program: _Program) -> None:
    # A str parameter the caller lends, a view parameter's loan, a literal's static storage.
    assert _stores(program, "Tok") == [("s", 0)]
    assert _stores(program, "Viewed") == [("v", 0)]
    assert [m for m, source in _stores(program, "Lit")] == ["s"]
    assert not isinstance(_stores(program, "Lit")[0][1], int)
    # The constructor's field write stores the loan, no copy and no exit.
    ctor = _lowered(program, "Tok.__init__")
    s, n = field_write(ctor, "s"), field_write(ctor, "n")
    assert isinstance(s.value, MIRAlias) and s.storage_write is None and isinstance(n.value, MIRRead)
    assert not ctor.exceptional_exits


def test_unmodeled_stored_loans_refuse(program: _Program) -> None:
    # The constructor's own copy dies at return: storing its loan into the
    # result escapes, and the escape is the constructor body's conflict.
    assert program.verdicts["Owning.__init__"].conflicts == ("store_escape",)


def test_a_base_leg_lends_the_derived_parameter_s_loan(program: _Program) -> None:
    # The base's call stores the derived parameter's loan, as a direct
    # `self.s = s` in the derived constructor would; a literal argument stores static storage.
    assert _stores(program, "Derived") == [("s", 0)]
    assert _stores(program, "ViewLent") == [("v", 0)]
    assert [m for m, source in _stores(program, "ViewLit")] == ["v"]
    assert not isinstance(_stores(program, "ViewLit")[0][1], int)
    # An inherited constructor calls the base's: its parameters are the base's.
    child, tok = _definition(program, "Child"), _definition(program, "Tok")
    assert child.callee == tok.callee and child.initializes == tok.layout.type


def test_a_recursive_record_holding_a_view_holds_a_borrow(program: _Program) -> None:
    # `kids` re-enters Tree; the frame that entered Tree sees its view member,
    # so the recursion is decided: Tree, and a record holding one, hold a borrow.
    with activate_compiler(program.compiler):
        assert loan_class(_record(program, "Tree")).holds is Loan.YES
    assert _definition(program, "Tree") == "record member holds a borrow"
    # A member record's own definition decides whether its loans are modeled.
    assert _definition(program, "Forest") == "member definition: record member holds a borrow"
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
    refused = lower_constructor(stored, MIRBodyId("m", "Tok.__init__"), definitions=program.definitions)
    assert isinstance(refused, MIRNotCovered), dump_function(refused)
    # The field write refuses it as any write of a view member does.
    assert refused.reason == "unsupported view source"


def test_a_record_without_a_definition_has_a_held_layout(program: _Program) -> None:
    forest, reader = _record(program, "Forest"), _record(program, "Reader")
    # Its member record has no definition: no caller constructs a Forest,
    # but a holder's layout is decided from the fields alone.
    assert isinstance(program.definitions.records[forest], str)
    assert isinstance(program.definitions.held_layout(None, forest), MIRHeldLayout)
    assert program.definitions.layouts[forest].fields[0].id.name == "first"
    assert program.definitions.layouts[reader].fields[0].id.name == "data"
    head = _lowered(program, "Reader.head")
    assert reader in {r.type for r in head.records} and not program.verdicts["Reader.head"].conflicts
    # A member record holding a loan keys it under the record around it.
    assert _record(program, "Holder") in program.definitions.layouts


def test_a_lent_member_keys_its_loans_under_the_record(program: _Program) -> None:
    # The parameter's seeded entry sits at the member path; the callee's
    # read of its parameter's loan resolves through the member's place.
    fn = _lowered(program, "lent_member")
    tok, member = _member(program, "Holder", "tok"), _member(program, "Tok", "s")
    key = MIRPlace(fn.slots[0].id, (tok, member))
    dependencies = prepared(fn).dependencies
    assert dependencies.objects[fn.slots[0].id] == (key,)
    assert dependencies.referents[MIRPoint(fn.entry, 0)][key] == frozenset({MIRReferent(key, True, held=True)})
    assert program.verdicts["lent_member"].conflicts == ()


# --- the dependency state ------------------------------------------------------------------

def _state_at_return(fn: MIRFunction):
    dependencies = prepared(fn).dependencies
    assert not isinstance(dependencies, MIRNotCovered), dependencies
    block = next(b for b in fn.blocks if isinstance(b.terminator, MIRReturn))
    return dependencies, dependencies.referents[MIRPoint(block.id, len(block.statements))]


def test_stored_loans_follow_the_object(program: _Program) -> None:
    fn = _lowered(program, "built")
    member = _member(program, "Tok", "s")
    _, state = _state_at_return(fn)
    buf = next(s.id for s in fn.slots if s.name == "buf")
    storage = [s.id for s in fn.slots if s.shape.is_storage and s.type == _record(program, "Tok")]
    # The construct's object, its copy and the copy moved on all store the buffer's loan.
    assert len(storage) == 3
    for sid in storage:
        assert state[MIRPlace(sid, (member,))] == frozenset({MIRReferent(MIRPlace(buf))})
    writes = {type(s.value) for s in _assigns(fn) if not s.target.projections and s.target.root in storage}
    assert writes == {MIRCall, MIRCopy, MIRMove}


def test_a_fill_with_no_stored_loan_to_carry_refuses(program: _Program) -> None:
    fn = _lowered(program, "built")
    slots = {s.id: s for s in fn.slots}
    layouts = fn.record_layouts
    objects = {sid: object_keys(slot, layouts) for sid, slot in slots.items()}
    tok = _record(program, "Tok")
    source, target = [s.id for s in fn.slots if s.shape.is_storage and s.type == tok][:2]
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
    assert resolve_referents(MIRPlace(fn.slots[0].id, (MIRDeref(), member)), state, {s.id: s for s in fn.slots},
                             fn.record_layouts) == {held}
    assert "held:%0.__main__.Tok::s" in dump_dependencies(dependencies)


def test_a_literal_member_stores_static_storage(program: _Program) -> None:
    fn = _lowered(program, "literal")
    member = _member(program, "Lit", "s")
    _, state = _state_at_return(fn)
    storage = next(s.id for s in fn.slots if s.shape.is_storage)
    loans = state[MIRPlace(storage, (member,))]
    assert len(loans) == 1 and next(iter(loans)).static


def test_an_object_with_no_stored_loan_entry_refuses(program: _Program) -> None:
    fn = _lowered(program, "seeded")
    tok = _record(program, "Tok")
    # With no layout of the record, no entry is seeded: the read refuses,
    # never an empty origin set.
    bare = replace(fn, records=tuple(r for r in fn.records if r.type != tok))
    # The first dependency run refuses: no field selection is proven either.
    validated(bare)
    with pytest.raises(MIRPresenceError, match="field selections not proven"):
        validate_function(bare)
    result = prepared(bare)
    assert isinstance(result, MIRNotCovered) and result.reason == "view member read of an object with no stored loan"
    param = fn.slots[0].id
    with pytest.raises(MIRUnseededLoan):
        resolve_referents(MIRPlace(param, (MIRDeref(), _member(program, "Tok", "s"))),
                          {MIRPlace(param): frozenset({MIRReferent(MIRPlace(param), external=True)})},
                          {s.id: s for s in fn.slots}, fn.record_layouts)


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
    records = fn.record_layouts
    member = _member(program, "Tok", "s")
    p, q = MIRSlotId(fn.id, 50), MIRSlotId(fn.id, 51)
    held = MIRReferent(MIRPlace(p, (member,)), external=True, held=True)
    sibling = MIRReferent(MIRPlace(p, (_member(program, "Tok", "n"),)), external=True)
    other = MIRReferent(MIRPlace(q), external=True)
    exclusions = storage_exclusions(slots, records)
    # A sibling field of the very object, or another external object: the
    # held rule runs before origins and paths are compared, either way.
    assert reaches(sibling, held, exclusions) and reaches(other, held, exclusions)
    assert reaches(held, sibling, exclusions) and reaches(held, other, exclusions)
    # Private and static storage are never what an external loan views.
    assert not reaches(MIRReferent(MIRPlace(q)), held, exclusions)
    assert not reaches(MIRReferent(MIRPlace(q), external=True, static=True), held, exclusions)
    assert not reaches(held, MIRReferent(MIRPlace(q)), exclusions)


# --- summaries ---------------------------------------------------------------------------------

def test_a_view_result_off_a_member_keeps_its_path(program: _Program) -> None:
    summary = program.verdicts["first"].summary
    assert summary.state is MIRSummaryState.KNOWN
    member = _member(program, "Tok", "s")
    path = (th.THIRFieldIdentity(member.id.owner, member.id.name, member.type),)
    assert summary.summary.returns == frozenset({MIRReturnOrigin(0, path)})
    # A view member is a return endpoint, never a write endpoint.
    writing = replace(summary.summary, writes=frozenset({MIRParameterWrite(0, path)}))
    assert summary_problem(writing, no_layouts()) == "unsupported call write field or access"


# --- the validator --------------------------------------------------------------------------

def test_a_view_member_place_is_read_whole(program: _Program) -> None:
    fn = _lowered(program, "built")
    holder = next(s.id for s in fn.slots if s.name == "t")
    read = next(s for s in _assigns(fn) if isinstance(s.value, MIRBorrow) and s.value.source.root == holder
                and s.value.source.projections)
    place = read.value.source
    # A view member is rebound only to the loan a view holder holds.
    with pytest.raises(MIRValidationError, match="view member write needs a view holder"):
        validate_function(_with_statement(fn, read, target=place, value=MIRCopy(place)))
    # Nothing projects through the loan.
    with pytest.raises(MIRValidationError, match="projection through a view member"):
        validate_function(_with_statement(fn, read, value=MIRBorrow(MIRPlace(place.root, (
            *place.projections, MIRDeref())))))


def test_a_construct_stores_a_view_operand(program: _Program) -> None:
    fn = _lowered(program, "built")
    construct = next(s for s in _assigns(fn) if isinstance(s.value, MIRCall) and s.value.summary.transfers)
    count = next(a for a in construct.value.arguments if fn.slots[a.index].shape.is_scalar)
    # A view member takes a lending holder, never a scalar.
    with pytest.raises(MIRValidationError, match="call argument type mismatch"):
        validate_function(_with_statement(fn, construct, value=replace(construct.value, arguments=(
            count, count))))


def test_constructor_view_member_validation(program: _Program) -> None:
    ctor = _lowered(program, "Tok.__init__")
    s, n = field_write(ctor, "s"), field_write(ctor, "n")

    def member(value: object) -> MIRFunction:
        return rewritten(ctor, s, replace(s, value=value))
    # A view member is bound to the loan a view holder holds: no copy, no borrow, no constant.
    for value in (MIRCopy(MIRPlace(s.value.source), True), MIRBorrow(n.value.source), MIRConstant("lit")):
        with pytest.raises(MIRValidationError, match="view member write needs a view holder"):
            validate_function(member(value))
    # A scalar lends nothing.
    with pytest.raises(MIRValidationError, match="view member write needs a view holder"):
        validate_function(member(MIRAlias(n.value.source.root)))


def test_storage_that_would_move_a_stored_loan_refuses(program: _Program) -> None:
    fn = _lowered(program, "seeded")
    tok = _record(program, "Tok")
    param = fn.slots[0].id
    # A wrapper payload or member: its selection would be needed to reach the object.
    for changes in (
            dict(shape=optional_shape(OptionalType(tok), borrowed_shape(tok, True))),
            dict(shape=union_shape(UnionType((tok, _record(program, "Lit"))),
                                   (borrowed_shape(tok, True), borrowed_shape(_record(program, "Lit"), True)))),
            dict(shape=tuple_shape(TupleType((tok,)), (borrowed_shape(tok, True),)))):
        with pytest.raises(MIRValidationError, match="wrapper holds a borrow"):
            validate_function(_with_slot(fn, param, **changes))
    # A record member holding a loan keys it under the outer record, which needs the member's layout.
    holder = _record(program, "Holder")
    outer = replace(fn.records[0], type=holder, fields=(MIRField(replace(fn.records[0].fields[0].id, owner=holder,
                                                                          name="tok"), tok),), ancestors=())
    validate_function(replace(fn, records=(*fn.records, outer)))
    with pytest.raises(MIRValidationError, match="record field needs its layout"):
        validate_function(replace(fn, records=(outer,)))


def test_whole_writes_of_a_record_holding_a_loan_validate(program: _Program) -> None:
    fn = _lowered(program, "built")
    tok = _record(program, "Tok")
    construct = next(s for s in _assigns(fn) if isinstance(s.value, MIRCall) and s.value.summary.transfers)
    # A result by value hands its stored loans to the caller as transfers.
    storage = construct.target.root
    block = next(b for b in fn.blocks if isinstance(b.terminator, MIRReturn))
    returned = replace(fn, return_type=OwnType(tok), blocks=tuple(
        replace(b, terminator=MIRReturn(storage)) if b is block else b for b in fn.blocks))
    validate_function(returned)

