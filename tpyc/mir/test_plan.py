"""The lowering plan's invariants: every node is an immutable value the
check pass decides once, and the emit pass folds only the nodes it knows."""

import dataclasses
import inspect

import pytest

from ..typesys import BOOL, INT32, VoidType
from . import plan as plan_module
from .emit import PlanEmitter
from .nodes import MIRBodyId, MIRBodyKind, MIRClass, MIRFunction, MIRShape
from .plan import PBinding, PBody, PConstant, PDeclare, PFullExpression, PReturnValue

PLAN_CLASSES = [cls for _, cls in inspect.getmembers(plan_module, inspect.isclass)
                if cls.__module__ == plan_module.__name__]


def _body(*statements: object) -> PBody:
    return PBody(MIRBodyKind.FREE_FUNCTION, VoidType(), True, None, (), (), statements, False, (), None, None, {})


def _build(*statements: object) -> MIRFunction:
    return PlanEmitter(MIRBodyId("main", "f"), _body(*statements)).build()


@pytest.mark.parametrize("cls", PLAN_CLASSES, ids=lambda cls: cls.__name__)
def test_plan_nodes_are_frozen_dataclasses(cls: type) -> None:
    assert dataclasses.is_dataclass(cls) and cls.__dataclass_params__.frozen


def test_plan_node_cannot_be_changed_after_the_check() -> None:
    constant = PConstant(None, INT32, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        constant.value = 2  # type: ignore[misc]


def test_fold_rejects_an_unknown_statement() -> None:
    with pytest.raises(TypeError, match="no lowering for plan node object"):
        _build(object())


def test_fold_rejects_an_unknown_value() -> None:
    binding = PBinding("x", MIRShape(INT32, MIRClass.LEAF))
    with pytest.raises(TypeError, match="no lowering for plan node PReturnValue"):
        _build(PDeclare(None, binding, PFullExpression(None, PReturnValue(None, None), False), None))


def test_fold_builds_a_planned_declaration() -> None:
    binding = PBinding("flag", MIRShape(BOOL, MIRClass.LEAF))
    fn = _build(PDeclare(None, binding, PFullExpression(None, PConstant(None, BOOL, True), False), None),
                PReturnValue(None, None))
    assert [s.name for s in fn.slots] == ["flag", None]
    assert len(fn.blocks) == 1
