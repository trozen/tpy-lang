"""Method receivers preserve identity and finalized access through alias holders."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.lower.functions import lower_function as lower_thir
from ..thir.testutil import _assert_rejects_at, _compile, _entry, _strict_reject
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from ..typesys import INT32, ReadonlyType
from .definitions import MIRDefinitions
from .lower import lower_function
from .nodes import MIRBodyId, MIRBodyKind, MIRFieldId, MIRFunction, MIRNotCovered, MIRSlotKind
from .testutil import Reference, execute


SOURCE = """\
from tpy import int32, readonly, copy
class Other:
    value: int32
    def __init__(self, value: int32):
        self.value = value
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def direct(self) -> int32:
        saved = self
        saved.value = 11
        return self.value
    def singleton(self) -> int32:
        packed = (self,)
        packed[0].value = 12
        return self.value
    def mixed(self) -> int32:
        packed = (self, self.value)
        packed[0].value = 13
        return packed[1]
    def inferred(self, other: Cell) -> int32:
        saved = self
        other.value = 14
        return saved.value
    @readonly
    def explicit(self) -> int32:
        saved = self
        return saved.value
    def reseat(self, other: Cell, choose: bool) -> int32:
        saved = self
        if choose:
            saved = other
        saved.value = 16
        return self.value
    def optional(self, other: Cell) -> int32:
        saved: Cell | None = self
        other.value = 17
        if saved is not None:
            return saved.value
        return 0
    def owned_copy(self) -> int32:
        saved = copy(self)
        self.value = 19
        return saved.value
    @staticmethod
    def static(value: int32) -> int32:
        return value
    @classmethod
    def class_value(cls) -> int32:
        return 4
    @property
    def property_value(self) -> int32:
        return self.value
    def __bool__(self) -> bool:
        return self.value != 0
class Outer:
    inner: Cell
    def __init__(self, value: int32):
        self.inner = Cell(value)
    def nested(self) -> int32:
        saved = self.inner
        self.inner.value = 20
        return saved.value
"""

Artifacts = tuple[dict[str, th.THIRFunction], MIRDefinitions]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    emitted, ctx = compiler.generate_code_and_thir(_entry(modules))
    assert emitted
    return ({node.name: fn for node, fn in ctx.thir_functions.items()},
            MIRDefinitions(tuple(ctx.thir_constructors.values())))


def lower(fn: th.THIRFunction, definitions: MIRDefinitions | None = None) -> MIRFunction:
    result = lower_function(fn, MIRBodyId("methods", fn.name), kind=MIRBodyKind.METHOD,
                            definitions=definitions)
    assert isinstance(result, MIRFunction), result
    return result


def uncovered(fn: th.THIRFunction, reason: str, kind: MIRBodyKind = MIRBodyKind.METHOD) -> None:
    result = lower_function(fn, MIRBodyId("methods", fn.name), kind=kind)
    assert isinstance(result, MIRNotCovered), result
    assert reason in result.reason


@pytest.mark.parametrize("name,expected,written", [
    ("direct", 11, 11), ("singleton", 12, 12), ("mixed", 1, 13), ("owned_copy", 1, 19),
])
def test_receiver_aliases_and_explicit_copy(artifacts: Artifacts, name: str,
                                          expected: int, written: int) -> None:
    functions, definitions = artifacts
    fn = lower(functions[name], definitions)
    receiver = fn.slots[0]
    assert receiver.name == "self" and receiver.kind is MIRSlotKind.PARAMETER
    value = MIRFieldId(receiver.type, "value")
    heap = {1: {value: 1}}
    assert execute(fn, Reference(1), heap=heap) == expected
    assert heap[1][value] == written


@pytest.mark.parametrize("shared", [False, True])
def test_receiver_holders_observe_other_aliases(artifacts: Artifacts, shared: bool) -> None:
    fn = lower(artifacts[0]["inferred"])
    value = MIRFieldId(fn.slots[0].type, "value")
    assert execute(fn, Reference(1), Reference(1 if shared else 2),
                   heap={1: {value: 1}, 2: {value: 2}}) == (14 if shared else 1)


def test_access_comes_from_finalized_method_verdict(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    for name in ("inferred", "explicit"):
        fn = functions[name]
        assert fn.receiver.readonly
        assert fn.body[0].alias_binding.reference.readonly
        assert lower(fn).slots[0].readonly
    # Inferred constness is absent from the receiver's expression type.
    fn = functions["inferred"]
    assert fn.body[0].init.result_type == fn.receiver.type
    assert not fn.params[0].borrowed_record.readonly
    assert not functions["direct"].receiver.readonly


@pytest.mark.parametrize("choose,shared", [(False, False), (True, False), (True, True)])
def test_reseating_local_does_not_reseat_receiver(artifacts: Artifacts, choose: bool, shared: bool) -> None:
    fn = lower(artifacts[0]["reseat"])
    value = MIRFieldId(fn.slots[0].type, "value")
    heap = {1: {value: 1}, 2: {value: 2}}
    assert execute(fn, Reference(1), Reference(1 if shared else 2), choose,
                   heap=heap) == (16 if shared or not choose else 1)
    assert heap[1 if shared or not choose else 2][value] == 16


def test_nested_receiver_place(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    fn = lower(functions["nested"])
    inner = MIRFieldId(fn.slots[0].type, "inner")
    value = MIRFieldId(functions["direct"].receiver.type, "value")
    assert execute(fn, Reference(1), heap={1: {inner: {value: 1}}}) == 20


@pytest.mark.parametrize("name", ["static", "class_value", "property_value", "__bool__"])
def test_nonordinary_method_kinds_have_no_receiver_fact(artifacts: Artifacts, name: str) -> None:
    fn = artifacts[0][name]
    assert fn.receiver is None
    uncovered(fn, "body kind and receiver mismatch")


@pytest.mark.parametrize("flags", [
    {"is_consuming": True}, {"is_auto_own_borrowing_clone": True},
    {"is_auto_own_consuming_clone": True}, {"auto_readonly_polarity": "strip"},
    {"auto_readonly_polarity": "apply"}, {"type_params": ["T"]},
])
def test_specialized_producer_does_not_grant_ordinary_receiver(flags: dict[str, object]) -> None:
    compiler, modules = _compile("""\
from tpy import int32
class Cell:
    def constant(self) -> int32:
        return 1
""")
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    node, fn = next((node, fn) for node, fn in ctx.thir_functions.items() if node.name == "constant")
    specialized = lower_thir(replace(node, **flags), entry.analyzer, self_type=fn.receiver.type)
    assert specialized is None or specialized.receiver is None


def test_receiver_and_body_kind_must_agree(artifacts: Artifacts) -> None:
    fn = artifacts[0]["direct"]
    uncovered(replace(fn, receiver=None), "body kind and receiver mismatch")
    uncovered(fn, "body kind and receiver mismatch", MIRBodyKind.FREE_FUNCTION)
    uncovered(fn, "unsupported body kind", MIRBodyKind.CONSTRUCTOR)
    uncovered(replace(fn, receiver=replace(fn.receiver, type=INT32)), "unsupported reference fact")
    uncovered(replace(fn, params=(th.THIRParam("self", INT32),)), "duplicate binding")
    with pytest.raises(THIRValidationError, match="invalid receiver fact"):
        validate_thir(replace(fn, receiver=replace(fn.receiver, readonly=1)))


def test_receiver_reads_and_alias_access_are_checked(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    fn = functions["direct"]
    alias = fn.body[0]
    uncovered(replace(fn, receiver=replace(fn.receiver, readonly=True)), "alias increases access")
    for changes, reason in (({"cpp": "__self"}, "unsupported metadata: cpp"),
                            ({"result_type": INT32}, "reference type mismatch"),
                            ({"form": th.Form.STORAGE}, "receiver read form"),
                            ({"result_type": ReadonlyType(fn.receiver.type)}, "receiver read increases access")):
        bad_alias = replace(alias, init=replace(alias.init, **changes))
        uncovered(replace(fn, body=(bad_alias, *fn.body[1:])), reason)
    bad_alias = replace(alias, alias_binding=replace(alias.alias_binding, source="other"))
    with pytest.raises(THIRValidationError, match="alias binding disagrees"):
        validate_thir(replace(fn, body=(bad_alias, *fn.body[1:])))
    # A readonly receiver cannot become writable by accessing its field directly.
    fn = functions["singleton"]
    store = replace(fn.body[1], target=replace(fn.body[1].target, receiver=th.THIRSelf(
        fn.receiver.type, form=th.Form.BORROW)))
    uncovered(replace(fn, receiver=replace(fn.receiver, readonly=True), body=(store, fn.body[2])),
              "readonly field store")


def test_receiver_cannot_be_reseated(artifacts: Artifacts) -> None:
    fn = artifacts[0]["direct"]
    alias = fn.body[0]
    reseat = th.THIRAssign(alias.init, alias.init, alias_binding=alias.alias_binding)
    uncovered(replace(fn, body=(reseat,)), "reference parameter reseat")


@pytest.mark.parametrize("binding,reason", [
    ("saved: Cell | Other = self", "decl.ptr_union_source"),
    ("saved: Cell | Other = other\n        saved = self", "decl.union_reseat_source"),
    ("saved: Cell | None = None\n        saved = self", "decl.opt_reseat_source"),
])
def test_self_wrapper_captures_retain_frontend_gates(binding: str, reason: str) -> None:
    source = SOURCE.replace("    def owned_copy", f"""    def wrapper(self, other: Cell) -> int32:
        {binding}
        return 0
    def owned_copy""")
    _, reasons = _strict_reject(source)
    _assert_rejects_at(reasons, "body:stmt.var_decl", shape=reason)


def test_self_optional_pointer_initializer_remains_uncovered(artifacts: Artifacts) -> None:
    uncovered(artifacts[0]["optional"], "optional backing storage")
