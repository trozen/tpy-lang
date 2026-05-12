"""Unit tests for cycle_detection module."""

import pytest
from .typesys import NominalType, RecordInfo, FieldInfo, PtrType, TypeParamRef, make_list
from .type_def_registry import (
    attach_dynamic_type_def, clear_dynamic_type_defs, TypeCategory,
)
from .cycle_detection import detect_type_cycles, TypeCycle


def _named(name: str) -> NominalType:
    return NominalType(name)


def _box(inner: NominalType) -> NominalType:
    # `_module_qname` mirrors what type resolution sets on real records;
    # it's what feeds `record_info_of(typ)` through the TypeDef registry.
    return NominalType("Box", type_args=(inner,), _module_qname="Box")


def _list(inner) -> NominalType:
    return make_list(element_type=inner)


@pytest.fixture
def _box_recordinfo():
    """Register a minimal RecordInfo for Box mirroring tplib.Box's
    `_ptr: Ptr[T]` storage. The cycle walker now reads indirection from
    record fields (with type-param substitution) rather than a hardcoded
    name list, so tests using _box() must register the record."""
    info = RecordInfo(
        name="Box",
        fields=[FieldInfo(name="_ptr", type=PtrType(TypeParamRef("T")))],
        type_params=["T"],
    )
    attach_dynamic_type_def("Box", TypeCategory.RECORD, record=info)
    try:
        yield info
    finally:
        clear_dynamic_type_defs()


@pytest.fixture
def _list_indirecting():
    """Flip is_indirecting=True on the static `builtins.list` TypeDef for
    the duration of one test. In production the `@builtin_type("builtins.list",
    indirecting=True)` stub registration handles this; the unit tests skip
    stub compilation, so we toggle the flag directly."""
    from .type_def_registry import get_type_def
    td = get_type_def("builtins.list")
    assert td is not None
    prev = td.is_indirecting
    td.is_indirecting = True
    try:
        yield
    finally:
        td.is_indirecting = prev


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

    def test_record_record_cycle_with_box(self, _box_recordinfo) -> None:
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

    def test_mutual_recursion_union_records(self, _box_recordinfo) -> None:
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

    def test_list_provides_indirection(self, _list_indirecting) -> None:
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

    def test_deep_cycle(self, _box_recordinfo) -> None:
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

    def test_no_false_positives_for_self_reference(self, _box_recordinfo) -> None:
        """Self-references are skipped (handled by D19)."""
        cycles = detect_type_cycles(
            record_fields={"Node": [("child", _box(_named("Node")))]},
            union_aliases={},
        )
        assert cycles == []

    def test_empty_input(self) -> None:
        cycles = detect_type_cycles({}, {})
        assert cycles == []

    def test_imported_record_self_loop_terminates(self) -> None:
        """The structural field walk would loop forever on an imported,
        non-indirecting record that references itself in its own fields
        (e.g. `class Wrapper[T]: inner: Wrapper[T]` imported from another
        module, where Wrapper is NOT in the current module's target_names).
        The walker's `expanding` recursion guard must terminate.
        """
        # Register Wrapper with a self-referential field. No Ptr, no
        # is_indirecting -- so the walker takes the structural-fields
        # path and would recurse indefinitely without the guard.
        wrapper_info = RecordInfo(
            name="Wrapper",
            fields=[FieldInfo(name="inner", type=NominalType(
                "Wrapper", type_args=(TypeParamRef("T"),), _module_qname="Wrapper",
            ))],
            type_params=["T"],
        )
        attach_dynamic_type_def("Wrapper", TypeCategory.RECORD, record=wrapper_info)
        try:
            # The cycle check should terminate (recursion guard fires) and
            # report no cycle: Foo's field type is Wrapper[A], which the
            # walker expands once into `inner: Wrapper[T->A]` -- the second
            # entry into Wrapper hits the guard and returns without finding
            # `A` in target_names along that path.
            cycles = detect_type_cycles(
                record_fields={
                    "Foo": [("w", NominalType(
                        "Wrapper",
                        type_args=(_named("A"),),
                        _module_qname="Wrapper",
                    ))],
                    "A": [("x", _named("int"))],
                },
                union_aliases={},
            )
            # The important assertion is termination; no cycle exists in
            # this graph (Foo -> A via Wrapper's substituted field, A -> int).
            assert cycles == []
        finally:
            clear_dynamic_type_defs()
