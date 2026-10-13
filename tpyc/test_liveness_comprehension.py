"""Tests for the comprehension half of `tpyc.liveness.analyze_last_uses`.

A comprehension's element, filters and later sources run once per item of
the clause before them, so a read there is a last use only of a variable
that clause binds; any other read (an enclosing name, an outer clause's
variable) runs again for the next item, and moving it would hand the next
item a moved-from object.
"""

from __future__ import annotations

from tpyc.identity_map import IdentitySet
from tpyc.liveness import analyze_last_uses
from tpyc.parse.nodes import (
    TpyComprehensionGenerator, TpyExprStmt, TpyListComprehension, TpyName,
    TpyVarDecl,
)


def _gen(var: str, iterable, *conditions) -> TpyComprehensionGenerator:
    return TpyComprehensionGenerator(var=var, iterable=iterable,
                                     conditions=list(conditions))


def _comp(element, *gens: TpyComprehensionGenerator) -> TpyListComprehension:
    return TpyListComprehension(element_expr=element, generators=list(gens))


def _run(expr) -> tuple[IdentitySet, IdentitySet]:
    repeated = IdentitySet()
    marks = analyze_last_uses([
        TpyVarDecl(name="s", type=None, init=None),
        TpyVarDecl(name="xs", type=None, init=None),
        TpyVarDecl(name="ys", type=None, init=None),
        TpyExprStmt(expr=expr),
    ], repeated_reads=repeated)
    return marks, repeated


def test_enclosing_name_in_the_element_is_never_a_last_use():
    s = TpyName(name="s")
    src = TpyName(name="xs")
    marks, repeated = _run(_comp(s, _gen("_", src)))
    assert s not in marks
    assert s in repeated
    # The first source runs once, before any item.
    assert src in marks


def test_the_clause_variable_in_the_element_is_a_last_use():
    x = TpyName(name="x")
    marks, _ = _run(_comp(x, _gen("x", TpyName(name="xs"))))
    assert x in marks


def test_an_outer_clause_variable_under_an_inner_clause_is_not():
    x = TpyName(name="x")
    inner_src = TpyName(name="ys")
    marks, repeated = _run(_comp(x, _gen("x", TpyName(name="xs")),
                                 _gen("_", inner_src)))
    assert x not in marks
    assert x in repeated
    # The inner source runs once per outer item and reads an enclosing name.
    assert inner_src not in marks


def test_an_outer_clause_variable_in_a_nested_element_is_not():
    x = TpyName(name="x")
    nested = _comp(x, _gen("_", TpyName(name="ys")))
    marks, _ = _run(_comp(nested, _gen("x", TpyName(name="xs"))))
    assert x not in marks


def test_an_outer_clause_variable_as_a_nested_first_source_is():
    x = TpyName(name="x")
    nested = _comp(TpyName(name="y"), _gen("y", x))
    marks, _ = _run(_comp(nested, _gen("x", TpyName(name="xs"))))
    assert x in marks


def test_a_filter_reads_its_own_clause_variable_as_a_last_use():
    x = TpyName(name="x")
    s = TpyName(name="s")
    marks, _ = _run(_comp(TpyName(name="x"), _gen("x", TpyName(name="xs"),
                                                  x)))
    # `x` is read twice in the statement (filter and element): no mark.
    assert x not in marks
    marks, repeated = _run(_comp(TpyName(name="k"),
                                 _gen("k", TpyName(name="xs"), s)))
    assert s not in marks and s in repeated
