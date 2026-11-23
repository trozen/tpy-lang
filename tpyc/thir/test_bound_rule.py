"""The handle bound is a closed classification: every expression node kind
has exactly one rule in `bound_rule`'s table, and every statement kind one
in `statement_hands`'s, so a node kind added to the parser without one
fails here rather than refusing or sharing a seat."""

import inspect

from tpyc.parse import nodes
from tpyc.thir.lower.frame_seat import _BOUND_RULES, _STMT_RULES


def _expr_kinds() -> set[type]:
    return {cls for _, cls in inspect.getmembers(nodes, inspect.isclass)
            if issubclass(cls, nodes.TpyExpr) and cls is not nodes.TpyExpr
            and cls.__module__ == nodes.__name__}


def test_every_expression_kind_has_a_rule() -> None:
    missing = sorted(c.__name__ for c in _expr_kinds()
                     if c not in _BOUND_RULES)
    assert not missing, f"no bound_rule entry for: {missing}"


def test_no_rule_for_a_non_expression() -> None:
    assert all(issubclass(c, nodes.TpyExpr) for c in _BOUND_RULES)


def _stmt_kinds() -> set[type]:
    return {cls for _, cls in inspect.getmembers(nodes, inspect.isclass)
            if issubclass(cls, nodes.TpyStmt) and cls is not nodes.TpyStmt
            and cls.__module__ == nodes.__name__}


def test_every_statement_kind_has_a_rule() -> None:
    missing = sorted(c.__name__ for c in _stmt_kinds()
                     if c not in _STMT_RULES)
    assert not missing, f"no statement_hands entry for: {missing}"


def test_no_statement_rule_for_a_non_statement() -> None:
    assert all(issubclass(c, nodes.TpyStmt) for c in _STMT_RULES)
