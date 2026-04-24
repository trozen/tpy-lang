"""C3 linearization and diamond detection invariants (D22 prep).

The multi-base path is still gated in sema, so these tests exercise
`c3_linearize`, `TypeRegistry.compute_mro_ancestors`, and
`TypeRegistry.detect_diamond` directly. Once D22 lifts the gate, the
algorithms these tests pin down start running in production.
"""
from __future__ import annotations

import pytest

from tpyc.typesys import (
    NominalType, RecordInfo, TypeRegistry,
    c3_linearize, C3LinearizationError,
    same_nominal_symbol_loose,
)
from tpyc.type_def_registry import (
    attach_dynamic_type_def, TypeCategory, clear_dynamic_type_defs,
)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    clear_dynamic_type_defs()


def _nom(name: str) -> NominalType:
    """NominalType with qname set so is_user_record resolves via the attached TypeDef."""
    return NominalType(name, _module_qname=f"m.{name}")


def _record(name: str, parents: list[NominalType], mro_ancestors: list[NominalType]) -> RecordInfo:
    info = RecordInfo(
        name=name, fields=[], module="m",
        parents=list(parents),
        mro_ancestors=list(mro_ancestors),
    )
    attach_dynamic_type_def(
        info.qualified_name(),
        TypeCategory.RECORD,
        record=info,
        is_value_type=False,
    )
    return info


def _type_names(types: list) -> list[str]:
    return [t.name for t in types]


# ---------------------------------------------------------------------------
# c3_linearize: pure algorithm
# ---------------------------------------------------------------------------

class TestC3Linearize:
    def test_empty_bases(self) -> None:
        assert c3_linearize([]) == []

    def test_single_base_no_ancestors(self) -> None:
        """class Child(Root): -> [Root]."""
        root = _nom("Root")
        assert _type_names(c3_linearize([(root, [])])) == ["Root"]

    def test_single_base_with_chain(self) -> None:
        """A <- B <- C. class D(C): -> [C, B, A]."""
        a, b, c = _nom("A"), _nom("B"), _nom("C")
        # C's own ancestry: [B, A]
        result = c3_linearize([(c, [b, a])])
        assert _type_names(result) == ["C", "B", "A"]

    def test_non_diamond_two_parents(self) -> None:
        """class D(B, C): with B(A), C: -> [B, A, C]."""
        a, b, c = _nom("A"), _nom("B"), _nom("C")
        result = c3_linearize([(b, [a]), (c, [])])
        assert _type_names(result) == ["B", "A", "C"]

    def test_canonical_diamond_c3(self) -> None:
        """Canonical C3: O <- A, O <- B, A <- X(A, B), result should include A then B.

        This is a 'diamond-free' compose since A and B don't share an ancestor in
        this reduced form. Here: class D(B, C) where B(A), C(A) -- A is shared.
        C3 linearizes this as [B, C, A], but our detect_diamond rejects it.
        The linearization algorithm itself is permissive.
        """
        a, b, c = _nom("A"), _nom("B"), _nom("C")
        # B.mro_ancestors = [A], C.mro_ancestors = [A]
        result = c3_linearize([(b, [a]), (c, [a])])
        assert _type_names(result) == ["B", "C", "A"]

    def test_inconsistent_hierarchy_raises(self) -> None:
        """class X(A, B) where A's MRO says [A, P, Q] and B's says [B, Q, P] -- no valid linearization."""
        a, b, p, q = _nom("A"), _nom("B"), _nom("P"), _nom("Q")
        with pytest.raises(C3LinearizationError):
            c3_linearize([(a, [p, q]), (b, [q, p])])

    def test_duplicate_direct_bases_raises(self) -> None:
        """class D(B, B) -- same base listed twice is inconsistent."""
        b = _nom("B")
        with pytest.raises(C3LinearizationError):
            c3_linearize([(b, []), (b, [])])


# ---------------------------------------------------------------------------
# TypeRegistry.compute_mro_ancestors + detect_diamond
# ---------------------------------------------------------------------------

class TestRegistryMRO:
    def test_leaf_record_mro_empty(self) -> None:
        reg = TypeRegistry()
        a = _record("A", parents=[], mro_ancestors=[])
        reg.register_record(a)
        assert reg.compute_mro_ancestors(a) == []

    def test_single_parent_linear(self) -> None:
        reg = TypeRegistry()
        a = _record("A", parents=[], mro_ancestors=[])
        reg.register_record(a)
        a_ref = _nom("A")
        b = _record("B", parents=[a_ref], mro_ancestors=[a_ref])
        reg.register_record(b)
        b_ref = _nom("B")
        c = _record("C", parents=[b_ref], mro_ancestors=[])
        reg.register_record(c)
        result = reg.compute_mro_ancestors(c)
        assert _type_names(result) == ["B", "A"]

    def test_multi_parent_no_diamond(self) -> None:
        reg = TypeRegistry()
        a = _record("A", parents=[], mro_ancestors=[])
        reg.register_record(a)
        b = _record("B", parents=[], mro_ancestors=[])
        reg.register_record(b)
        a_ref, b_ref = _nom("A"), _nom("B")
        d = _record("D", parents=[a_ref, b_ref], mro_ancestors=[])
        reg.register_record(d)
        result = reg.compute_mro_ancestors(d)
        assert _type_names(result) == ["A", "B"]


class TestDiamondDetection:
    def test_single_parent_no_diamond(self) -> None:
        reg = TypeRegistry()
        a = _record("A", parents=[], mro_ancestors=[])
        reg.register_record(a)
        a_ref = _nom("A")
        b = _record("B", parents=[a_ref], mro_ancestors=[a_ref])
        reg.register_record(b)
        assert reg.detect_diamond(b) is None

    def test_unrelated_multi_parent_no_diamond(self) -> None:
        reg = TypeRegistry()
        a = _record("A", parents=[], mro_ancestors=[])
        b = _record("B", parents=[], mro_ancestors=[])
        reg.register_record(a)
        reg.register_record(b)
        d = _record("D", parents=[_nom("A"), _nom("B")], mro_ancestors=[])
        reg.register_record(d)
        assert reg.detect_diamond(d) is None

    def test_classic_diamond_detected(self) -> None:
        """A at top; B(A), C(A); D(B, C) -- A is reachable via both B and C."""
        reg = TypeRegistry()
        a = _record("A", parents=[], mro_ancestors=[])
        reg.register_record(a)
        a_ref = _nom("A")
        b = _record("B", parents=[a_ref], mro_ancestors=[a_ref])
        c = _record("C", parents=[a_ref], mro_ancestors=[a_ref])
        reg.register_record(b)
        reg.register_record(c)
        d = _record("D", parents=[_nom("B"), _nom("C")], mro_ancestors=[])
        reg.register_record(d)

        diamond = reg.detect_diamond(d)
        assert diamond is not None
        anc, p1, p2 = diamond
        assert anc == "A"
        assert {p1, p2} == {"B", "C"}

    def test_direct_and_indirect_parent_is_diamond(self) -> None:
        """class C(A, B) where B(A) -- A is both a direct base and an ancestor of B."""
        reg = TypeRegistry()
        a = _record("A", parents=[], mro_ancestors=[])
        reg.register_record(a)
        a_ref = _nom("A")
        b = _record("B", parents=[a_ref], mro_ancestors=[a_ref])
        reg.register_record(b)
        c = _record("C", parents=[a_ref, _nom("B")], mro_ancestors=[])
        reg.register_record(c)

        diamond = reg.detect_diamond(c)
        assert diamond is not None
        anc, p1, p2 = diamond
        assert anc == "A"
        assert {p1, p2} == {"A", "B"}

    def test_duplicate_direct_parent_is_diamond(self) -> None:
        """class D(B, B) -- same parent listed twice."""
        reg = TypeRegistry()
        b = _record("B", parents=[], mro_ancestors=[])
        reg.register_record(b)
        b_ref = _nom("B")
        d = _record("D", parents=[b_ref, b_ref], mro_ancestors=[])
        reg.register_record(d)

        diamond = reg.detect_diamond(d)
        assert diamond is not None
        assert diamond[0] == "B"
