"""Nested or-pattern groups are flattened at parse time.

Every downstream `match` consumer treats an or-pattern's alternative list as
terminal, so the parser is where the invariant is established.
"""

from .nodes import (
    TpyAsPattern, TpyClassPattern, TpyMatch, TpyOrPattern,
)
from .parser import Parser


def _first_case_pattern(source: str):
    module = Parser().parse(source)
    fn = module.functions[0]
    stmt = fn.body[0]
    assert isinstance(stmt, TpyMatch)
    return stmt.cases[0].pattern


def test_nested_groups_flatten_to_one_alternative_list():
    pattern = _first_case_pattern(
        "def f(x: A | B | C | D) -> int:\n"
        "    match x:\n"
        "        case ((A() | B()) | C()) | D():\n"
        "            return 1\n"
    )
    assert isinstance(pattern, TpyOrPattern)
    assert all(isinstance(alt, TpyClassPattern) for alt in pattern.patterns)
    assert [alt.cls.name for alt in pattern.patterns] == ["A", "B", "C", "D"]


def test_as_alternative_keeps_its_nested_group():
    pattern = _first_case_pattern(
        "def f(x: A | B | C) -> int:\n"
        "    match x:\n"
        "        case ((A() | B()) as y) | C():\n"
        "            return 1\n"
    )
    assert isinstance(pattern, TpyOrPattern)
    assert len(pattern.patterns) == 2
    bound = pattern.patterns[0]
    assert isinstance(bound, TpyAsPattern)
    assert bound.name == "y"
    assert isinstance(bound.pattern, TpyOrPattern)
    assert len(bound.pattern.patterns) == 2
