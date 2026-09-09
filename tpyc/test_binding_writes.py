"""Binding analysis covers parser shapes independently of native admission."""

from textwrap import indent

import pytest

from .parse import Parser
from .parse.nodes import TpyForEach, TpyStmt, body_writes_name
from .prescan import collect_fact_kills


def _loop_body(source: str) -> list[TpyStmt]:
    module = Parser().parse(
        "def f():\n    for i in range(3):\n" + indent(source, "        ") + "\n"
    )
    loop = module.functions[0].body[0]
    assert isinstance(loop, TpyForEach)
    return loop.body


@pytest.mark.parametrize(
    ("source", "writes_target"),
    [
        pytest.param("i = 0", True, id="assign"),
        pytest.param("i += 1", True, id="augassign"),
        pytest.param("print(i := 0)", True, id="walrus"),
        pytest.param(
            "for i in range(2):\n    print(i)", True, id="nested-scalar-target"
        ),
        pytest.param(
            "for i, j in [(1, 2)]:\n    print(i)",
            True,
            id="nested-tuple-target",
        ),
        pytest.param("i, j = (1, 2)", True, id="tuple-assignment"),
        pytest.param(
            "match x:\n    case i:\n        print(i)", True, id="match-capture"
        ),
        pytest.param("with cm as i:\n    print(i)", True, id="with-target"),
        pytest.param(
            "try:\n    f()\nexcept ValueError as i:\n    print(i)",
            True,
            id="except-target",
        ),
        pytest.param("del i", True, id="del"),
        pytest.param(
            "try:\n    continue\nfinally:\n    i = 0", True, id="finally"
        ),
        pytest.param(
            "xs = [(i := 0) for j in range(1)]", True, id="comprehension-walrus"
        ),
        pytest.param(
            "xs = [j for j in range(1) if (i := 0) == 0]",
            True,
            id="comprehension-filter-walrus",
        ),
        pytest.param(
            "def write():\n    nonlocal i\n    i = 0\nwrite()",
            False,
            id="nested-nonlocal-is-separate-body",
        ),
        pytest.param(
            "def write():\n    global i\n    i = 0\nwrite()",
            False,
            id="nested-global-is-separate-body",
        ),
        pytest.param("print(i)", False, id="read-only"),
        pytest.param(
            "xs = [i for i in range(3)]", False, id="comprehension-local-target"
        ),
        pytest.param(
            "def write():\n    i = 0\n    print(i)\nwrite()",
            False,
            id="nested-def-local-target",
        ),
    ],
)
def test_body_binding_scope(source: str, writes_target: bool) -> None:
    # Native admission cannot cover every binder in this shared AST contract.
    assert body_writes_name(_loop_body(source), "i") is writes_target


@pytest.mark.parametrize(
    ("declaration", "kills_target"),
    [
        pytest.param("nonlocal i\n", True, id="nonlocal"),
        pytest.param("global i\n", True, id="global"),
        pytest.param("", False, id="local-shadow"),
    ],
)
def test_nested_scope_fact_kills(declaration: str, kills_target: bool) -> None:
    source = "def write():\n" + indent(declaration + "i = 0", "    ") + "\nwrite()"
    # An outer loop or earlier i assignment would mask missed closure effects.
    kills = collect_fact_kills(_loop_body(source))
    assert ("i" in kills.names) is kills_target
