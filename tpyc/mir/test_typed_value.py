"""A fragment's value is one typed `MIRValue`: what its shape says, the
holder each kind of value is lent through, the place an assignment
target names, the one leaf-access rule and the one call write-path walk."""

import re
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ..compilation_context import activate_compiler
from ..mir_workspace import analyze_call_workspace
from ..thir import nodes as th
from ..thir.scalar_leaves import MIRClass
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing
from ..typesys import INT32, STR, STRVIEW, NominalType, OwnType, TupleType
from .call_contract import (
    MIR_STATIC, MIRContainerElements, MIRLoanTransfer, MIRParameterWrite, call_path_problem,
)
from .collect import call_definitions, dump_codegen_mir, enumerate_body_sources, lower_body
from .definitions import MIRDefinitions
from .emit import owned_leaf_shape, view_holder_shape
from .lower import _Lowering
from .nodes import MIRAssign, MIRConstant, MIRRead, MIRTupleCopy
from .value import (
    MIRField, MIRFieldId, MIRHolding, MIRRecordLayout, MIRRole, MIRShape, MIRValidationError, holder_form,
    with_access,
)
from .shapes import step
from .testutil import bound
from .validate import validate_function

SOURCE = '''\
from tpy import int32, readonly, String


class P:
    def __init__(self, x: int32) -> None:
        self.x = x


class R:
    p: P

    def __init__(self, x: int32) -> None:
        self.p = P(x)


class Bag:
    def __init__(self, pts: list[P]) -> None:
        self.pts = pts

    def total(self) -> int32:
        n = 0
        for p in self.pts:
            n += p.x
        return n


g: int32 = 0


def maybe(p: P | None) -> int32:
    if p is None:
        return 0
    return p.x


def pass_name(p: P) -> int32:
    return maybe(p)


def set_global(v: int32) -> int32:
    global g
    g = v
    return g


def set_local(v: int32) -> int32:
    n = 0
    n = v
    return n


def copy_tuple(t: tuple[int32, int32]) -> int32:
    u = t
    return u[0]


def reassign_tuple(t: tuple[int32, int32]) -> int32:
    u = (1, 2)
    u = t
    return u[0]


def read_narrowed(o: int32 | None) -> int32:
    if o is not None:
        return o
    return 0


def head(xs: readonly[list[int32]]) -> int32:
    return xs[0]


def narrow(r: R) -> readonly[P]:
    r.p.x = 1
    return r.p


def narrow_name(p: P) -> readonly[P]:
    p.x = 1
    return p


def takes(s: str) -> int32:
    return len(s)


def lend_string(s: String) -> int32:
    return takes(s)
'''


@pytest.fixture(scope="module")
def unit() -> SimpleNamespace:
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(entry)
        definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
        workspace = analyze_call_workspace(call_definitions(ctx, entry.name), definitions)
        dump = dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name, definitions,
                                compiler.thir_reject_by_node, workspace)
        sources = {s.body.declaration.split("@", 1)[0]: s for s in enumerate_body_sources(
            entry.ast, entry.analyzer, ctx, entry.name, compiler.thir_reject_by_node)}
    bodies: dict[str, list[str]] = {}
    for line in dump.splitlines():
        if line.startswith("fn "):
            name = line.split("::", 1)[1].split("@", 1)[0]
            bodies[name] = [line]
        elif bodies:
            bodies[name].append(re.sub(r" @ \d+:\d+$", "", re.sub(r" residence=r\d+| duration=\w+", "", line.strip())))
    return SimpleNamespace(compiler=compiler, bodies=bodies, sources=sources, definitions=definitions,
                           workspace=workspace)


@pytest.fixture(scope="module")
def dumps(unit) -> dict[str, list[str]]:
    return unit.bodies


def _lowered(unit, name: str):
    with activate_compiler(unit.compiler):
        return lower_body(unit.sources[name], unit.definitions, unit.workspace, fresh=True)


def test_a_container_field_is_lent_with_the_members_registered_by_then(dumps) -> None:
    # The field's shape is derived at the read, after its element record registered.
    lines = dumps["Bag.total"]
    assert "%3: list[P] container-ref readonly element=P:record:readonly temporary" in lines
    assert "%3 = borrow (*%0).__main__.Bag::pts" in lines


def test_a_record_name_binds_an_optional_parameter_through_its_own_holder(dumps) -> None:
    # The name's holder is the record pointer the payload copies.
    assert "%1 = present %0" in dumps["pass_name"]


def test_assignment_targets_name_the_place_a_read_does(dumps) -> None:
    glob = dumps["set_global"]
    assert "%2: int32 global main::g" in glob and "%2 = read %1" in glob
    local = dumps["set_local"]
    assert "%2: int32 local n" in local and local.index("%3 = read %0") + 1 == local.index("%2 = read %3")
    assert "%2 = read (*%0.payload)" not in dumps["read_narrowed"]
    assert any(re.fullmatch(r"%\d+ = read %0\.payload", line) for line in dumps["read_narrowed"])


def test_a_tuple_name_is_copied_into_a_fresh_holder(dumps) -> None:
    lines = dumps["copy_tuple"]
    assert "%1: tuple[int32, int32] payload(value, value) temporary" in lines
    assert lines[lines.index("bb0:") + 1:lines.index("bb0:") + 3] == ["%1 = tuple-copy %0", "%2 = tuple-copy %1"]


@pytest.mark.parametrize("name", ["copy_tuple", "reassign_tuple"])
def test_a_tuple_copy_states_the_shape_of_the_slot_it_is_written_into(unit, monkeypatch, name: str) -> None:
    seen: list[tuple[MIRShape, MIRShape]] = []
    whole = _Lowering.whole

    def spy(self, body, loc, needs_region=False):
        if isinstance(body.value.value, MIRTupleCopy):
            seen.append((body.shape_of(), self.bound("u")))
        return whole(self, body, loc, needs_region)
    monkeypatch.setattr(_Lowering, "whole", spy)
    _lowered(unit, name)
    # The declaration's and the reassignment's copy alike: the binding's shape.
    assert len(seen) == (2 if name == "reassign_tuple" else 1)
    assert all(value == bound and value.cls is MIRClass.TUPLE for value, bound in seen)


def test_holder_form_is_each_holder_a_value_is_lent_through() -> None:
    point = NominalType("P")
    pointer = MIRShape(point, MIRClass.RECORD, MIRHolding.POINTER, True)
    assert holder_form(MIRShape(point, MIRClass.RECORD, readonly=True)) == pointer
    assert holder_form(owned_leaf_shape(STR, MIRRole.STORAGE)) == owned_leaf_shape(STR, MIRRole.LOAN)
    assert holder_form(view_holder_shape(STR)) == view_holder_shape(STR)
    members = (MIRShape(INT32, MIRClass.LEAF, readonly=True),)
    container = MIRShape(NominalType("list", (INT32,)), MIRClass.CONTAINER, readonly=True, members=members)
    assert holder_form(container) == MIRShape(container.type, MIRClass.CONTAINER, MIRHolding.REGION, True, members)
    pair = MIRShape(TupleType((INT32, INT32)), MIRClass.TUPLE, members=(members[0], members[0]))
    assert holder_form(pair) == pair


def test_a_leaf_member_of_a_readonly_holder_is_readonly() -> None:
    leaf = MIRShape(INT32, MIRClass.LEAF)
    members = with_access((leaf,), True)
    holder = MIRShape(NominalType("list", (INT32,)), MIRClass.CONTAINER, MIRHolding.REGION, True, members)
    # The holder's members and the step to its element agree.
    assert members[0].readonly and step(holder, MIRContainerElements(), {}) == members[0]


def test_a_leaf_element_of_a_readonly_container_reads_readonly(unit, dumps) -> None:
    assert "%0: list[int32] container-ref readonly element=int32:leaf:readonly parameter xs" in dumps["head"]
    # The container's own access refuses the write with or without the leaf
    # rule; this pins that the readonly element stays unwritable.
    fn = _lowered(unit, "head")
    block = next(b for b in fn.blocks if any(isinstance(s, MIRAssign) and isinstance(s.value, MIRRead)
                                             for s in b.statements))
    index, read = next((i, s) for i, s in enumerate(block.statements)
                       if isinstance(s, MIRAssign) and isinstance(s.value, MIRRead))
    # The element the body reads, written instead.
    write = MIRAssign(read.value.source, MIRConstant(5), read.loc)
    damaged = replace(fn, blocks=tuple(
        replace(b, statements=(*b.statements[:index], write, *b.statements[index + 1:])) if b is block else b
        for b in fn.blocks))
    with pytest.raises(MIRValidationError, match="store through readonly container"):
        validate_function(damaged)


def test_a_field_returned_readonly_is_lent_through_a_readonly_holder(dumps) -> None:
    # The receiver is lent mutably; the result asks for readonly access.
    lines = dumps["narrow"]
    assert "%0: R mutable-ref parameter r" in lines and "%2: P readonly-ref temporary" in lines
    assert "%2 = borrow (*%0).__main__.R::p" in lines and "return %2" in lines


def test_a_mutable_name_returned_readonly_is_its_own_holder(dumps) -> None:
    lines = dumps["narrow_name"]
    assert "%0: P mutable-ref parameter p" in lines and "return %0" in lines
    assert not any(re.match(r"%\d+: P ", line) for line in lines if "parameter" not in line)


# --- the call write-path walk (`call_path_problem`) ----------------------------------

_R = NominalType("R", _module_qname="m.R")
_V = th.THIRFieldIdentity(_R, "v", STRVIEW)
_MISSING = th.THIRFieldIdentity(_R, "missing", STRVIEW)
_LAYOUT = MIRRecordLayout(_R, (MIRField(MIRFieldId(_R, "v"), STRVIEW),), True, True)
_OWNED = bound(OwnType(_R), ParamPassing.OWN)
_LENT = bound(_R, ParamPassing.MUT_REF, ref=th.THIRBorrowedRecord(_R, False))


def _summary(transfers=(), writes=()) -> SimpleNamespace:
    return SimpleNamespace(parameters=(_OWNED, _LENT), transfers=tuple(transfers), writes=frozenset(writes),
                           returns=frozenset())


def _walk(summary, skip=None) -> tuple[str | None, list[tuple[str, object]]]:
    log: list[tuple[str, object]] = []

    def storage_of(parameter: int) -> NominalType:
        log.append(("storage", parameter))
        return _R

    def layout_of(record: NominalType) -> MIRRecordLayout:
        log.append(("layout", record))
        return _LAYOUT
    return call_path_problem(summary, INT32, storage_of, layout_of, skip), log


def test_owned_transfer_ends_are_walked_before_any_parameter_path() -> None:
    bad_write = MIRParameterWrite(1, (_MISSING,))
    problem, log = _walk(_summary([MIRLoanTransfer(0, (_MISSING,), MIR_STATIC)], [bad_write]))
    assert problem == "call write field does not match record layout" and log == [("layout", _R)]
    problem, log = _walk(_summary([MIRLoanTransfer(0, (_V,), MIR_STATIC)], [bad_write]))
    assert problem == "call write field does not match record layout"
    assert log == [("layout", _R), ("storage", 1), ("layout", _R)]


def test_writes_are_walked_before_lent_transfer_paths() -> None:
    summary = _summary([MIRLoanTransfer(1, (_MISSING,), MIR_STATIC)], [MIRParameterWrite(1, (_V,))])
    problem, log = _walk(summary)
    assert problem == "call write field does not match record layout"
    assert log == [("storage", 1), ("layout", _R), ("storage", 1), ("layout", _R)]


def test_skip_passes_over_a_parameter_ended_transfer_path() -> None:
    summary = _summary([MIRLoanTransfer(1, (_MISSING,), MIR_STATIC)])
    assert _walk(summary)[0] == "call write field does not match record layout"
    problem, log = _walk(summary, skip=lambda parameter, storage: parameter == 1 and storage == _R)
    assert problem is None and log == [("storage", 1)]
