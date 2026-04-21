"""Unit tests for cycle_detection module."""

import pytest
from .typesys import NominalType, RecordInfo, FieldInfo, make_list
from .cycle_detection import detect_type_cycles, TypeCycle


def _named(name: str) -> NominalType:
    return NominalType(name)


def _box(inner: NominalType) -> NominalType:
    return NominalType("Box", type_args=(inner,))


def _list(inner) -> NominalType:
    return make_list(element_type=inner)


class TestCycleDetection:
    """Tests for detect_type_cycles."""

    def test_no_cycle(self) -> None:
        """A -> B with no back-reference: no cycle."""
        cycles = detect_type_cycles(
            record_fields={"A": [("b", _named("B"))], "B": [("x", _named("int"))]},
            union_aliases={},
        )
        assert cycles == []

    def test_record_record_cycle_no_indirection(self) -> None:
        """A has B, B has A -- infinite size cycle."""
        cycles = detect_type_cycles(
            record_fields={
                "A": [("b", _named("B"))],
                "B": [("a", _named("A"))],
            },
            union_aliases={},
        )
        assert len(cycles) == 1
        assert cycles[0].is_fully_unindirected()
        assert set(cycles[0].path) == {"A", "B"}

    def test_record_record_cycle_with_box(self) -> None:
        """A has Box[B], B has A -- cycle broken by Box on one edge."""
        cycles = detect_type_cycles(
            record_fields={
                "A": [("b", _box(_named("B")))],
                "B": [("a", _named("A"))],
            },
            union_aliases={},
        )
        assert len(cycles) == 1
        # One edge has indirection (Box), so cycle is valid
        assert cycles[0].has_any_indirection()
        assert not cycles[0].is_fully_unindirected()

    def test_mutual_recursion_union_records(self) -> None:
        """type Expr = Lit | BinOp, where BinOp has Box[Expr] -- the AST pattern."""
        cycles = detect_type_cycles(
            record_fields={
                "Lit": [("value", _named("int"))],
                "BinOp": [("left", _box(_named("Expr"))), ("right", _box(_named("Expr")))],
            },
            union_aliases={
                "Expr": (_named("Lit"), _named("BinOp")),
            },
        )
        assert len(cycles) >= 1
        # At least one cycle should involve Expr
        expr_cycles = [c for c in cycles if "Expr" in c.path]
        assert len(expr_cycles) >= 1
        # The cycle has indirection (Box on one edge), so it's valid
        for c in expr_cycles:
            assert c.has_any_indirection()
            assert not c.is_fully_unindirected()
        # Expr should be identified as an alias in the cycle
        for c in expr_cycles:
            assert "Expr" in c.alias_names

    def test_mutual_recursion_no_box_error(self) -> None:
        """type Expr = Lit | BinOp, where BinOp has bare Expr -- infinite size."""
        cycles = detect_type_cycles(
            record_fields={
                "Lit": [("value", _named("int"))],
                "BinOp": [("left", _named("Expr")), ("right", _named("Expr"))],
            },
            union_aliases={
                "Expr": (_named("Lit"), _named("BinOp")),
            },
        )
        assert len(cycles) >= 1
        expr_cycles = [c for c in cycles if "Expr" in c.path]
        assert len(expr_cycles) >= 1
        # All edges lack indirection -- infinite size
        assert any(c.is_fully_unindirected() for c in expr_cycles)

    def test_list_provides_indirection(self) -> None:
        """type Expr = Lit | Call, where Call has list[Expr] -- OK."""
        cycles = detect_type_cycles(
            record_fields={
                "Lit": [("value", _named("int"))],
                "Call": [("args", _list(_named("Expr")))],
            },
            union_aliases={
                "Expr": (_named("Lit"), _named("Call")),
            },
        )
        expr_cycles = [c for c in cycles if "Expr" in c.path]
        assert len(expr_cycles) >= 1
        for c in expr_cycles:
            assert c.has_any_indirection()
            assert not c.is_fully_unindirected()

    def test_deep_cycle(self) -> None:
        """A -> B -> C -> A through records, depth > 1."""
        cycles = detect_type_cycles(
            record_fields={
                "A": [("b", _box(_named("B")))],
                "B": [("c", _named("C"))],
                "C": [("a", _named("A"))],
            },
            union_aliases={},
        )
        assert len(cycles) >= 1
        deep = [c for c in cycles if len(c.path) == 3]
        assert len(deep) >= 1
        assert set(deep[0].path) == {"A", "B", "C"}

    def test_no_false_positives_for_self_reference(self) -> None:
        """Self-references are skipped (handled by D19)."""
        cycles = detect_type_cycles(
            record_fields={"Node": [("child", _box(_named("Node")))]},
            union_aliases={},
        )
        assert cycles == []

    def test_empty_input(self) -> None:
        cycles = detect_type_cycles({}, {})
        assert cycles == []
