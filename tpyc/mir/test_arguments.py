"""The one argument ladder (`_Lowering.argument`): a call and a constructor
bind each argument by its parameter binding's class and holding; the
constructor-only admission refuses what a construct does not take yet."""

import pytest

from ..thir.testutil import _compile, _entry
from .collect import MIRBodyVerdict, MIRVerdictStatus, enumerate_bodies
from .nodes import MIRAssign, MIRBorrow, MIRPlace, MIRSlotKind

SOURCE = '''\
from tpy import int32, Own, copy, readonly


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class D(P):
    pass


class Ignore:
    x: int32

    def __init__(self, p: P) -> None:
        self.x = 1


class W:
    p: P

    def __init__(self, p: P) -> None:
        self.p = copy(p)


class O:
    p: P

    def __init__(self, p: Own[P]) -> None:
        self.p = p


def unused_descendant() -> int32:
    d = D(2)
    obj = Ignore(d)
    return obj.x


def named_own(p: P) -> int32:
    x = O(p)
    return x.p.x


def lent_temporary() -> int32:
    w = W(P(2))
    return w.p.x


class V:
    x: float

    def __init__(self, x: float) -> None:
        self.x = x

    @readonly
    def muls(self, s: float) -> Own["V"]:
        return V(self.x * s)

    @readonly
    def add(self, o: "V") -> Own["V"]:
        return V(self.x + o.x)


def call_temporary(a: readonly[V], b: readonly[V]) -> float:
    return a.add(b.muls(3.0)).x
'''


@pytest.fixture(scope="module")
def verdicts() -> dict[str, MIRBodyVerdict]:
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        found = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                 compiler.thir_reject_by_node, mir.workspace)
    return {v.body.declaration.split("@")[0]: v for v in found}


@pytest.mark.parametrize("name, reason", [
    # A descendant lent to a constructor parameter, refused whether or not a member uses it.
    ("unused_descendant", "constructor argument type"),
    # A named record handed over to a constructor's `Own[P]` parameter.
    ("named_own", "constructor argument type"),
])
def test_refused_arguments_name_their_reason(verdicts: dict[str, MIRBodyVerdict], name: str, reason: str) -> None:
    verdict = verdicts[name]
    assert verdict.status is MIRVerdictStatus.UNCOVERED, verdict.describe()
    assert verdict.reason == reason


@pytest.mark.parametrize("name", [
    "lent_temporary",
    # A call's bare record temporary (no THIR argument temporary) takes the constructor's route.
    "call_temporary",
])
def test_a_lent_record_temporary_is_a_readonly_loan(verdicts: dict[str, MIRBodyVerdict], name: str) -> None:
    fn = verdicts[name].function
    assert fn is not None, verdicts[name].describe()
    slots = {s.id: s for s in fn.slots}
    # The holder a construct's operand is: a temporary borrowing region storage.
    loans = [s for b in fn.blocks for s in b.statements
             if isinstance(s, MIRAssign) and isinstance(s.value, MIRBorrow)
             and isinstance(s.value.source, MIRPlace) and not s.value.source.projections
             and slots[s.value.source.root].shape.is_storage and slots[s.target.root].kind is MIRSlotKind.TEMPORARY]
    assert len(loans) == 1
    assert slots[loans[0].target.root].readonly
