"""A constructor's body builds the record it returns: its member-init list
initializes every field before any other use of `self`."""

from dataclasses import replace
from collections.abc import Callable

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..typesys import BOOL, INT32, STR, STRVIEW, NominalType
from .collect import enumerate_bodies
from .definitions import MIRDefinitions
from .dump import dump_function
from .testutil import lower_constructor, lower_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRBranch, MIRCall, MIRConstant, MIRCopy,
    MIRFunction, MIRGoto, MIRMove, MIRNotCovered, MIRPlace, MIRRead, MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageInit,
    MIRStorageWrite, MIRStorageWriteMode, MIRWrap, statement_call,
)
from .shapes import MIRDeref
from .storage_evidence import analyze_escapes
from .validate import MIRDefiniteAssignmentError, statement_reads, value_may_raise
from .value import MIRFieldId, MIRValidationError, value_shape
from .testutil import bind_parameters, compiled_definitions, construct, leaf_shape, no_layouts, prepared, reslot
from .prepare import validate_function


SOURCE = """\
from tpy import int32
class Cell:
    value: int32
    before: int32
    changed: bool
    def __init__(self, value: int32, reset: bool):
        self.value = value
        self.before = 0
        self.changed = False
        direct = self
        single = (self,)
        saved = (self, self.value)
        if reset:
            direct.value = 5
            single[0].value = 6
            saved[0].value = 7
        self.before = saved[1]
        self.changed = reset
class Plain:
    value: int32
    def __init__(self, value: int32):
        self.value = value
class Early:
    ready: bool
    def __init__(self, ready: bool):
        self.ready = ready
        if ready:
            return
        while not self.ready:
            self.ready = True
class Empty:
    def __init__(self):
        pass
def create(value: int32) -> int32:
    cell = Cell(value, True)
    return cell.value
"""


Artifacts = tuple[dict[str, th.THIRConstructor], dict[str, th.THIRFunction]]


@pytest.fixture(scope="module")
def compiled():
    return _compile(SOURCE)


@pytest.fixture(scope="module")
def artifacts(compiled) -> Artifacts:
    compiler, modules = compiled
    with activate_compiler(compiler):
        emitted, ctx = compiler.generate_code_and_thir(_entry(modules))
        assert emitted
        return ({ctor.record_name: ctor for ctor in ctx.thir_constructors.values()},
                {node.name: fn for node, fn in ctx.thir_functions.items()})


def lower(ctor: th.THIRConstructor) -> MIRFunction:
    result = lower_constructor(ctor, MIRBodyId("constructors", ctor.record_name))
    assert isinstance(result, MIRFunction), result
    return result


def built(fn: MIRFunction) -> tuple[MIRSlot, MIRSlot]:
    """The record a constructor builds, and its `self` bound to it."""
    result = next(s for s in fn.slots if s.destination)
    holder = next(s for s in fn.slots if s.bound_storage == result.id)
    return result, holder


def statements(fn: MIRFunction) -> list[object]:
    return [s for b in fn.blocks for s in b.statements]


def field_write(fn: MIRFunction, name: str) -> MIRAssign:
    """The first write of field `name` through `self`."""
    _, holder = built(fn)
    return next(s for s in statements(fn) if isinstance(s, MIRAssign) and s.target.root == holder.id
                and len(s.target.projections) == 2 and s.target.projections[1].id.name == name)


def rewritten(fn: MIRFunction, old: object, *new: object) -> MIRFunction:
    """`fn` with statement `old` replaced by `new` (none: dropped)."""
    return replace(fn, blocks=tuple(replace(b, statements=tuple(
        n for s in b.statements for n in ((s,) if s is not old else new))) for b in fn.blocks))


@pytest.mark.parametrize("reset", [False, True])
def test_initialization_precedes_alias_and_scalar_capture(artifacts: Artifacts, reset: bool) -> None:
    constructors, _ = artifacts
    fn = lower(constructors["Cell"])
    result, holder = built(fn)
    # The record is the body's own storage, begun at entry; `self` is a holder bound to it.
    assert result.kind is MIRSlotKind.TEMPORARY and holder.kind is MIRSlotKind.LOCAL and holder.name == "self"
    assert fn.blocks[0].statements[:2] == (MIRStorageInit(MIRPlace(result.id), None, None),
                                           MIRAssign(MIRPlace(holder.id), MIRBorrow(MIRPlace(result.id))))
    flat = statements(fn)
    writes = [i for i, s in enumerate(flat) if isinstance(s, MIRAssign) and s.target.root == holder.id
              and s.target.projections[:1] == (MIRDeref(),)]
    assert [flat[i].target.projections[1].id.name for i in writes[:3]] == ["value", "before", "changed"]
    # No other use of `self` comes before its fields are initialized.
    first_use = next(i for i, s in enumerate(flat) if holder.id in statement_reads(s) and i not in writes)
    assert first_use > writes[2]
    heap = {42: {}}
    construct(fn, 41, 3, reset, heap=heap)
    typ = result.type
    assert heap[41] == {MIRFieldId(typ, "value"): 7 if reset else 3,
                        MIRFieldId(typ, "before"): 3, MIRFieldId(typ, "changed"): reset}
    assert heap[42] == {}
    assert f"begin-storage %{result.id.index}" in dump_function(fn)


@pytest.mark.parametrize("ready", [False, True])
def test_entry_runs_once_before_early_return_or_loop(artifacts: Artifacts, ready: bool) -> None:
    fn = lower(artifacts[0]["Early"])
    heap = {}
    construct(fn, 8, ready, heap=heap)
    assert heap[8] == {MIRFieldId(built(fn)[0].type, "ready"): True}


def test_empty_record_and_nonmovable_record(artifacts: Artifacts) -> None:
    empty = lower(artifacts[0]["Empty"])
    heap = {}
    construct(empty, 1, heap=heap)
    assert heap == {1: {}}
    # The record is built where its caller receives it: nothing moves, so a
    # record that does not move is built all the same.
    ctor = artifacts[0]["Plain"]
    fn = lower(replace(ctor, record_layout=replace(ctor.record_layout, movable=False)))
    construct(fn, 2, 9, heap=heap)
    assert heap[2] == {MIRFieldId(built(fn)[0].type, "value"): 9}


def test_a_caller_applies_the_constructor_bodys_summary(artifacts: Artifacts) -> None:
    # `Cell`'s body aliases `self` and branches: its summary, not its
    # statements, is what `create`'s construction applies.
    constructors, functions = artifacts
    lower(constructors["Cell"])
    result = lower_function(functions["create"], MIRBodyId("constructors", "create"),
                            definitions=MIRDefinitions(tuple(constructors.values())))
    assert isinstance(result, MIRFunction), result
    call, = (s.value for s in statements(result) if isinstance(s, MIRAssign) and isinstance(s.value, MIRCall))
    assert call.summary.callee.identity.name == "__init__" and call.summary.normal_return_only


@pytest.mark.parametrize("change,reason", [
    (lambda c: replace(c, mil_inits=c.mil_inits[:-1]), "incomplete constructor"),
    (lambda c: replace(c, mil_inits=c.mil_inits * 2), "duplicate constructor field"),
    (lambda c: replace(c, mil_inits=(replace(c.mil_inits[0], move=True),)), "move"),
    (lambda c: replace(c, mil_inits=(replace(c.mil_inits[0], field_identity=None),)), "field identity"),
    (lambda c: replace(c, record_layout=None), "missing record layout"),
    (lambda c: replace(c, params=(replace(c.params[0], type=BOOL),)), "name type mismatch"),
    (lambda c: replace(c, base_inits=(th.THIRBaseInit("Base", (), NominalType("Base")),)),
     "base constructor identity"),
])
def test_incomplete_or_unsupported_initialization_fails_closed(
    artifacts: Artifacts, change: Callable[[th.THIRConstructor], th.THIRConstructor], reason: str,
) -> None:
    result = lower_constructor(change(artifacts[0]["Plain"]), MIRBodyId("constructors", "bad"))
    assert isinstance(result, MIRNotCovered) and reason in result.reason


def test_constructor_parameter_needs_published_passing(artifacts: Artifacts) -> None:
    ctor = artifacts[0]["Plain"]
    bad = replace(ctor, params=(replace(ctor.params[0], passing=None),))
    assert MIRDefinitions((bad,)).records[ctor.record_layout.type] == "unpublished parameter passing"


@pytest.mark.parametrize("value", [MIRConstant(True), MIRConstant(2**31),
                                   MIRRead(MIRPlace(MIRSlotId(MIRBodyId("other", "body"), 0)))])
def test_malformed_field_initializations_are_rejected(artifacts: Artifacts, value: object) -> None:
    fn = lower(artifacts[0]["Plain"])
    write = field_write(fn, "value")
    with pytest.raises(MIRValidationError):
        validate_function(rewritten(fn, write, replace(write, value=value)))


def test_every_field_is_initialized_before_the_record_is_used(artifacts: Artifacts) -> None:
    fn = lower(artifacts[0]["Cell"])
    _, holder = built(fn)
    write = field_write(fn, "value")
    # Returned with a field never initialized.
    with pytest.raises(MIRDefiniteAssignmentError, match="before every field is initialized"):
        validate_function(rewritten(fn, field_write(fn, "changed")))
    # A field read before its initialization.
    scalar = next(s for s in fn.slots if s.kind is MIRSlotKind.TEMPORARY and s.type == INT32)
    early = MIRAssign(MIRPlace(scalar.id), MIRRead(MIRPlace(holder.id, write.target.projections)))
    with pytest.raises(MIRDefiniteAssignmentError, match="read before field initialization"):
        validate_function(rewritten(fn, write, early, write))
    # `self` itself used before every field is initialized.
    direct = next(s for s in fn.slots if s.name == "direct")
    alias = MIRAssign(MIRPlace(direct.id), MIRAlias(holder.id))
    with pytest.raises(MIRDefiniteAssignmentError, match="use of the record before every field is initialized"):
        validate_function(rewritten(fn, write, alias, write))


def test_self_is_bound_once_to_the_record_it_builds(artifacts: Artifacts) -> None:
    fn = lower(artifacts[0]["Cell"])
    result, holder = built(fn)
    with pytest.raises(MIRValidationError, match="invalid bound storage"):
        validate_function(replace(fn, slots=tuple(reslot(s, readonly=True) if s.id == holder.id else s
                                                  for s in fn.slots)))
    # Only a constructor builds its record where its caller receives it.
    with pytest.raises(MIRValidationError, match="constructor result mismatch"):
        validate_function(replace(fn, slots=tuple(replace(s, destination=False, bound_storage=None)
                                                  if s.id in (result.id, holder.id) else s for s in fn.slots)))
    # Bound to storage that is not the record built.
    other = next(s for s in fn.slots if s.name == "direct")
    with pytest.raises(MIRValidationError, match="invalid bound storage"):
        validate_function(replace(fn, slots=tuple(replace(s, bound_storage=other.id) if s.id == holder.id else s
                                                  for s in fn.slots)))
    with pytest.raises(MIRValidationError, match="invalid slot kind"):
        validate_function(replace(fn, slots=(replace(fn.slots[0], kind="parameter"), *fn.slots[1:])))


@pytest.mark.parametrize("fields,body,reason", [
    ("value: int32 = 4", "pass", "incomplete constructor"),
    ("value: int32", "saved = value\n        self.value = saved", "incomplete constructor"),
    ("value: int32", "self.value = value\n        print((value, value))", "print argument needs a scalar leaf"),
])
def test_actual_emitted_exclusions(fields: str, body: str, reason: str) -> None:
    source = f"from tpy import int32\nclass Record:\n    {fields}\n    def __init__(self, value: int32):\n        {body}\n"
    compiler, modules = _compile(source)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(_entry(modules))
        ctor = next(c for c in ctx.thir_constructors.values() if c.record_name == "Record")
        result = lower_constructor(ctor, MIRBodyId("constructors", "excluded"))
        assert isinstance(result, MIRNotCovered) and reason in result.reason


@pytest.mark.parametrize("field,param,body", [
    ("value: int32", "value: int32", "self.value = value + 1"),
    ("value: str", "value: str", "self.value = value + '!'"),
])
def test_a_derived_initializer_is_an_ordinary_statement(field: str, param: str, body: str) -> None:
    # The member-init entry lowers as the field write it is, whatever its value.
    source = f"from tpy import int32\nclass Record:\n    {field}\n    def __init__(self, {param}):\n        {body}\n"
    compiler, modules = _compile(source)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(_entry(modules))
        ctor = next(c for c in ctx.thir_constructors.values() if c.record_name == "Record")
        fn = lower_constructor(ctor, MIRBodyId("constructors", "derived"))
        assert isinstance(fn, MIRFunction), fn
        assert fn.exceptional_exits


def test_owned_leaf_field_exclusions() -> None:
    source = ("from tpy import Own, StrView\nclass Record:\n    pair: tuple[str, str]\n"
              "    def __init__(self, value: str):\n        self.pair = (value, value)\n")
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    ctor = next(c for c in ctx.thir_constructors.values() if c.record_name == "Record")
    with activate_compiler(compiler):
        result = lower_constructor(ctor, MIRBodyId("constructors", "excluded"))
    assert isinstance(result, MIRNotCovered) and "unsupported record fields" in result.reason


def test_a_view_of_the_constructors_own_copy_escapes_with_the_record() -> None:
    # The record returned stores a loan of the constructor's own copy, which dies at return.
    source = ("from tpy import Own, StrView\nclass Record:\n    view: StrView\n"
              "    def __init__(self, value: Own[str]):\n        self.view = value\n")
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    ctor = next(c for c in ctx.thir_constructors.values() if c.record_name == "Record")
    with activate_compiler(compiler):
        fn = lower_constructor(ctor, MIRBodyId("constructors", "escape"))
        assert isinstance(fn, MIRFunction), fn
        (escape,) = analyze_escapes(prepared(fn)).stores
    value = next(s for s in fn.slots if s.name == "value")
    assert escape.origin == MIRPlace(value.id)


def test_initialization_uses_logical_fields_not_cpp_spellings(artifacts: Artifacts) -> None:
    ctor = artifacts[0]["Cell"]
    renamed = replace(ctor, record_name="Spelling", mil_inits=tuple(
        replace(mil, field_cpp="unrelated") for mil in ctor.mil_inits))
    fn = lower_constructor(ctor, MIRBodyId("constructors", "identity"))
    other = lower_constructor(renamed, MIRBodyId("constructors", "identity"))
    assert dump_function(fn) == dump_function(other)
    # C++ initializes in layout order: a list out of it is not modeled.
    reordered = lower_constructor(replace(ctor, mil_inits=tuple(reversed(ctor.mil_inits))),
                                  MIRBodyId("constructors", "order"))
    assert isinstance(reordered, MIRNotCovered) and reordered.reason == "constructor field order"


ENTRY_SOURCE = """\
from tpy import Own, StrView, String, int32, readonly
class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
class Entry:
    n: int32
    s: str
    v: StrView
    w: StrView
    p: P
    q: P
    r: readonly[P | None]
    u: String
    t: str
    def __init__(self, n: int32, s: str, w: StrView, p: P, q: Own[P], u: String, o: Own[str]) -> None:
        self.n = n
        self.s = s
        self.v = s
        self.w = w
        self.p = p
        self.q = q
        self.r = None
        self.u = u
        self.t = o
class OwnView:
    s: StrView
    def __init__(self, s: Own[str]) -> None:
        self.s = s
"""


@pytest.fixture(scope="module")
def entry_program():
    compiler, modules = _compile(ENTRY_SOURCE)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(_entry(modules))
        ctors = {ctor.record_name: ctor for ctor in ctx.thir_constructors.values()}
        definitions = compiled_definitions(compiler, tuple(ctors.values()))
        fn = lower_constructor(ctors["Entry"], MIRBodyId("constructors", "Entry"), definitions=definitions)
        assert isinstance(fn, MIRFunction), fn
        return compiler, fn, definitions, ctors


@pytest.fixture
def entry(entry_program):
    # Field and declared-type checks resolve under the compilation that registered them.
    compiler, *rest = entry_program
    with activate_compiler(compiler):
        yield rest


def test_member_inits_are_the_cfg_field_writes(entry) -> None:
    fn, _, _ = entry
    validate_function(fn)
    name = {s.name: s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER}
    deref = (MIRDeref(),)
    initialize = MIRStorageWrite(MIRStorageWriteMode.INITIALIZE)
    # Storage a field owns is initialized; a scalar or a view member holds no storage.
    expected = {
        "s": (MIRCopy(MIRPlace(name["s"], deref), True), initialize),
        "w": (MIRAlias(name["w"]), None),
        "p": (MIRCopy(MIRPlace(name["p"], deref)), initialize),
        "q": (MIRMove(name["q"]), initialize),
        "r": (MIRWrap(0), initialize),
        "u": (MIRCopy(MIRPlace(name["u"], deref), True), initialize),
        "t": (MIRMove(name["o"]), initialize),
    }
    for field, (value, fact) in expected.items():
        write = field_write(fn, field)
        assert (write.value, write.storage_write) == (value, fact), field
    # A scalar reads its parameter through a temporary, a view member a
    # holder of the lent parameter's loan, as any field write does.
    assert isinstance(field_write(fn, "n").value, MIRRead) and field_write(fn, "n").storage_write is None
    assert isinstance(field_write(fn, "v").value, MIRAlias)


def test_an_initialization_is_checked_against_the_fields_state(entry) -> None:
    fn, definitions, ctors = entry
    _, holder = built(fn)
    s, t = field_write(fn, "s"), field_write(fn, "t")
    # A field's own storage is no source before it is initialized.
    with pytest.raises(MIRDefiniteAssignmentError, match="read before field initialization"):
        validate_function(rewritten(fn, s, replace(s, value=MIRCopy(MIRPlace(holder.id, t.target.projections),
                                                                    True))))
    # The first write of a field initializes it; a replacement would need a value there.
    with pytest.raises(MIRValidationError, match="field write needs its initialization or replacement fact"):
        validate_function(rewritten(fn, s, replace(s, storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE))))
    # A `String` copy reads through a readonly lend only (a mutable holder of
    # an owned leaf is no slot at all).
    name = {s.name: s for s in fn.slots if s.kind is MIRSlotKind.PARAMETER and not s.shape.is_storage}
    with pytest.raises(MIRValidationError, match="mutable loan of a readonly value"):
        validate_function(bind_parameters(replace(fn, slots=tuple(
            reslot(s, readonly=False) if s.id == name["u"].id else s for s in fn.slots))))
    # A view member of the constructor's own copy escapes with the record
    # its body returns: the body's conflict, not its certificate's refusal.
    own_view = lower_constructor(ctors["OwnView"], MIRBodyId("constructors", "OwnView"), definitions=definitions)
    assert isinstance(own_view, MIRFunction), own_view
    (escape,) = analyze_escapes(prepared(own_view)).stores
    assert escape.origin == MIRPlace(next(s.id for s in own_view.slots if s.name == "s"))


def test_a_write_under_a_field_reads_it(entry) -> None:
    fn, _, _ = entry
    _, holder = built(fn)
    p = field_write(fn, "p")
    n = next(s for s in fn.slots if s.name == "n")
    point = next(r for r in fn.records if r.type.name == "P")
    # `self.p.x = n` before `self.p` is initialized writes into no record.
    under = MIRAssign(MIRPlace(holder.id, (*p.target.projections, point.fields[0])), MIRRead(MIRPlace(n.id)))
    with pytest.raises(MIRDefiniteAssignmentError, match="write under a field before its initialization"):
        validate_function(rewritten(fn, p, under, p))
    validate_function(rewritten(fn, p, p, under))


def test_a_readonly_field_is_initialized_never_replaced(entry) -> None:
    fn, _, _ = entry
    r = field_write(fn, "r")
    # `r: readonly[P | None]`: its initialization is allowed, a second write replaces it.
    validate_function(fn)
    replaced = replace(r, storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE))
    with pytest.raises(MIRValidationError, match="store through readonly storage"):
        validate_function(rewritten(fn, r, r, replaced))


def test_a_readonly_field_written_where_it_may_be_initialized_is_refused(entry) -> None:
    fn, _, _ = entry
    r = field_write(fn, "r")
    block, = fn.blocks
    index = block.statements.index(r)
    flag = MIRSlotId(fn.id, max(s.id.index for s in fn.slots) + 1)
    yes, join = MIRBlockId(fn.id, 1), MIRBlockId(fn.id, 2)

    def branched(first: tuple[object, ...], second: object) -> MIRFunction:
        """`r` written by `first` on one arm of a branch, then by `second` after the join."""
        return replace(fn, slots=(*fn.slots, MIRSlot(flag, leaf_shape(BOOL), MIRSlotKind.LOCAL, residence=block.region)),
                       blocks=(replace(block, statements=(*block.statements[:index],
                                                          MIRAssign(MIRPlace(flag), MIRConstant(True))),
                                       terminator=MIRBranch(flag, yes, join)),
                               MIRBlock(yes, first, MIRGoto(join), block.region),
                               MIRBlock(join, (second, *block.statements[index + 1:]), block.terminator,
                                        block.region)))

    validate_function(branched((), r))
    # Initialized on one path only, the field may hold its value: neither its initialization
    # nor a replacement, which its declared readonly would forbid anyway.
    for second in (r, replace(r, storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE))):
        with pytest.raises(MIRValidationError, match="write of a field initialized on some path only"):
            validate_function(branched((r,), second))


FROZEN_SOURCE = """\
from dataclasses import dataclass
from tpy import StrView, int32


@dataclass(frozen=True)
class Frozen:
    s: StrView
    n: int32
"""


def test_a_frozen_dataclass_constructor_initializes_its_view_and_scalar() -> None:
    compiler, modules = _compile(FROZEN_SOURCE)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(_entry(modules))
        ctor, = ctx.thir_constructors.values()
        fn = lower_constructor(ctor, MIRBodyId("constructors", "Frozen"),
                               definitions=compiled_definitions(compiler, (ctor,)))
        assert isinstance(fn, MIRFunction), fn
        validate_function(fn)
        # A view member and a scalar hold no storage: each is written once, with no storage
        # fact. Frozenness is no readonly field type but the readonly `self` every other body
        # binds, through which sema refuses a later write ("Cannot mutate readonly reference").
        for name in ("s", "n"):
            assert field_write(fn, name).storage_write is None
            assert sum(1 for st in statements(fn) if isinstance(st, MIRAssign)
                       and st.target == field_write(fn, name).target) == 1


RAISE_SOURCE = """\
from tpy import int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Quiet:
    p: P | None

    def __init__(self) -> None:
        self.p = None


class Loud:
    p: P | None
    s: str

    def __init__(self, s: str) -> None:
        self.p = None
        self.s = s


def quiet() -> int32:
    q = Quiet()
    return 1


def loud(s: str) -> int32:
    q = Loud(s)
    return len(q.s)
"""


def test_a_construction_raises_as_its_constructor_body_exits() -> None:
    compiler, modules = _compile(RAISE_SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        verdicts = {v.body.declaration.split("@")[0]: v for v in enumerate_bodies(
            entry.ast, entry.analyzer, ctx, entry.name, mir.definitions, compiler.thir_reject_by_node, mir.workspace)}
    for caller, record in (("quiet", "Quiet"), ("loud", "Loud")):
        summary = verdicts[f"{record}.__init__"].summary.summary
        call, = (c for b in verdicts[caller].function.blocks for s in b.statements
                 if (c := statement_call(s)) is not None and c.summary == summary)
        # One verdict for one construction: an empty Optional member raises nowhere, a
        # copied `str` member may; the caller's call raises exactly as the body exits.
        assert call.may_raise is (not summary.normal_return_only)
        assert verdicts[caller].exceptional_exits is call.may_raise
    assert verdicts["Quiet.__init__"].summary.summary.normal_return_only
    assert not verdicts["Loud.__init__"].summary.summary.normal_return_only


MEMBER_WRITE_SOURCE = """\
from tpy import copy, int32, nomove, readonly


@nomove
class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Q:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Fixed:
    p: P

    def __init__(self, x: int32) -> None:
        self.p = P(x)


class Frozen:
    q: readonly[Q]

    def __init__(self, x: int32) -> None:
        self.q = Q(x)


class Refrozen:
    q: readonly[Q]

    def __init__(self, x: int32) -> None:
        self.q = Q(x)
        self.q = Q(x)


class Text:
    s: readonly[str]

    def __init__(self, s: str) -> None:
        self.s = s


class Retext:
    s: readonly[str]
    n: int32

    def __init__(self, s: str) -> None:
        self.n = 0
        self.s = s
        self.s = s


def replace_fixed(h: Fixed, p: P) -> None:
    h.p = copy(p)
"""


@pytest.fixture(scope="module")
def member_writes():
    compiler, modules = _compile(MEMBER_WRITE_SOURCE)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(_entry(modules))
        ctors = {ctor.record_name: ctor for ctor in ctx.thir_constructors.values()}
        definitions = compiled_definitions(compiler, tuple(ctors.values()))
        lowered = {name: lower_constructor(ctor, MIRBodyId("constructors", name), definitions=definitions)
                   for name, ctor in ctors.items()}
        lowered.update({node.name: lower_function(fn, MIRBodyId("constructors", node.name), definitions=definitions)
                        for node, fn in ctx.thir_functions.items()})
        return compiler, lowered


def test_a_nonmovable_member_is_initialized_never_replaced(member_writes) -> None:
    compiler, lowered = member_writes
    # C++ constructs a member-init entry in place, but move-assigns a replacement.
    fixed = lowered["Fixed"]
    assert isinstance(fixed, MIRFunction), fixed
    with activate_compiler(compiler):
        assert field_write(fixed, "p").storage_write == MIRStorageWrite(MIRStorageWriteMode.INITIALIZE)
    replaced = lowered["replace_fixed"]
    assert isinstance(replaced, MIRNotCovered) and replaced.reason == "record member replacement needs movable record"


def test_a_readonly_record_member_is_initialized_never_replaced(member_writes) -> None:
    compiler, lowered = member_writes
    # A field's own `readonly` allows its first initialization, not a later store.
    frozen = lowered["Frozen"]
    assert isinstance(frozen, MIRFunction), frozen
    with activate_compiler(compiler):
        assert field_write(frozen, "q").storage_write == MIRStorageWrite(MIRStorageWriteMode.INITIALIZE)
    refrozen = lowered["Refrozen"]
    assert isinstance(refrozen, MIRNotCovered) and refrozen.reason == "readonly field store"


def test_a_readonly_owned_leaf_field_is_unmodeled(member_writes) -> None:
    _, lowered = member_writes
    # `readonly[str]` has no field layout, so the record is refused before any write of the
    # field reaches its own refusal, from a member-init entry or from a body statement.
    for name in ("Text", "Retext"):
        result = lowered[name]
        assert isinstance(result, MIRNotCovered) and result.reason == "unsupported record fields", name


def test_a_constant_raises_only_where_it_is_materialized() -> None:
    records = no_layouts()
    assert value_may_raise(MIRConstant("lit"), value_shape(STR), records)
    assert not value_may_raise(MIRConstant("lit"), value_shape(STRVIEW), records)
    assert not value_may_raise(MIRConstant(1), value_shape(INT32), records)
