"""THIR facts of constructor parameters: a record parameter publishes the
borrowed-record fact a function parameter does, at the constructor's own
const verdict; an `Own[R]` parameter publishes none (it is owned, never a
borrowed slot); and THIR validation checks a constructor parameter's facts
with the function-parameter checker."""

from dataclasses import replace

import pytest

from . import nodes as th
from .test_inherited_records import Program
from .validate import THIRValidationError, validate_constructor

SOURCE = """\
from tpy import int32, Own


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Reads:
    x: int32

    def __init__(self, p: Point) -> None:
        self.x = p.x


class Bumps:
    x: int32

    def __init__(self, p: Point) -> None:
        p.x += 1
        self.x = p.x


class Takes:
    a: Point

    def __init__(self, p: Own[Point]) -> None:
        self.a = p


def bump(p: Point) -> int32:
    p.x += 1
    return p.x
"""


@pytest.fixture(scope="module")
def program() -> Program:
    return Program(SOURCE)


def test_a_record_parameter_carries_the_constructors_const_verdict(program: Program) -> None:
    point = program.type("Point")
    p, = program.ctors["Reads"].params
    assert p.borrowed_record == th.THIRBorrowedRecord(point, True)
    p, = program.ctors["Bumps"].params
    assert p.borrowed_record == th.THIRBorrowedRecord(point, False)
    # The same fact a function parameter mutated the same way publishes.
    assert program.functions[(None, "bump")].params[0].borrowed_record == p.borrowed_record


def test_an_owned_record_parameter_is_no_borrowed_record(program: Program) -> None:
    p, = program.ctors["Takes"].params
    assert p.borrowed_record is None


def test_a_constructor_parameter_fact_must_agree_with_its_type(program: Program) -> None:
    ctor = program.ctors["Reads"]
    validate_constructor(ctor)
    p, = ctor.params
    wrong = replace(p, borrowed_record=th.THIRBorrowedRecord(program.type("Reads"), True))
    with pytest.raises(THIRValidationError, match="borrowed record fact disagrees with parameter"):
        validate_constructor(replace(ctor, params=(wrong,)))
    # An owned parameter given a borrowed fact disagrees with its `Own[...]` type.
    takes = program.ctors["Takes"]
    p, = takes.params
    wrong = replace(p, borrowed_record=th.THIRBorrowedRecord(program.type("Point"), False))
    with pytest.raises(THIRValidationError, match="borrowed record fact disagrees with parameter"):
        validate_constructor(replace(takes, params=(wrong,)))
