"""Pin the qname-aware nominal identity invariants.

Three concerns the test guards against:

1. `NominalType` equality + hash includes `_module_qname` -- two
   records with the same short name from different modules are
   distinct in sets / dicts / unions, and set dedup is insertion-
   order independent (proper equivalence relation).
2. `TypeRegistry.get_record_for_type` is qname-first -- when the
   NominalType carries a qname, the lookup routes to the matching
   record even if a locally-registered record shadows the short name.
   Covers both records registered via `register_record(info, name)`
   and records that land in the registry only via `register_module`.
3. `TypeRegistry.is_subclass_of` works for cross-module child/base
   pairs whose short names are shadowed by local records.
"""
from __future__ import annotations

import pytest

from tpyc.typesys import (
    ModuleInfo, NominalType, RecordInfo, TypeRegistry,
    make_union, UnionType, same_nominal_symbol_loose,
)
from tpyc.type_def_registry import (
    attach_dynamic_type_def, TypeCategory, clear_dynamic_type_defs,
)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    clear_dynamic_type_defs()


def _attach_record_td(info: RecordInfo) -> None:
    """Attach a TypeDef.record entry so `is_user_record` resolves --
    mirrors what sema's `register_record` does in production."""
    attach_dynamic_type_def(
        info.qualified_name(),
        TypeCategory.RECORD,
        record=info,
        is_value_type=False,
    )


# ---------------------------------------------------------------------------
# 1. Equality / hash invariant
# ---------------------------------------------------------------------------

class TestNominalEquality:
    def test_cross_module_same_name_unequal(self) -> None:
        a = NominalType("Foo", _module_qname="pkg_a.Foo")
        b = NominalType("Foo", _module_qname="pkg_b.Foo")
        assert a != b
        assert hash(a) != hash(b) or a != b  # hashes may collide, equality must not

    def test_bare_and_qname_bearing_unequal(self) -> None:
        # Strict contract: bare != qname-bearing.  Transitional sites
        # that want the loose bridge must call
        # `same_nominal_symbol_loose` explicitly.
        bare = NominalType("Foo")
        qn = NominalType("Foo", _module_qname="mod.Foo")
        assert bare != qn

    def test_set_dedup_order_independent(self) -> None:
        a = NominalType("Foo", _module_qname="pkg_a.Foo")
        b = NominalType("Foo", _module_qname="pkg_b.Foo")
        c = NominalType("Foo")
        # Three pairwise-unequal elements -> always 3 in the set
        # regardless of insertion order (guards against the non-
        # transitive-equality trap).
        assert len({a, b, c}) == 3
        assert len({c, a, b}) == 3
        assert len({b, c, a}) == 3

    def test_make_union_keeps_cross_module_distinct(self) -> None:
        a = NominalType("Foo", _module_qname="pkg_a.Foo")
        b = NominalType("Foo", _module_qname="pkg_b.Foo")
        u = make_union(a, b)
        assert isinstance(u, UnionType)
        assert len(u.members) == 2

    def test_same_qname_equal(self) -> None:
        a = NominalType("Foo", _module_qname="mod.Foo")
        b = NominalType("Foo", _module_qname="mod.Foo")
        assert a == b
        assert hash(a) == hash(b)


# ---------------------------------------------------------------------------
# 2. same_nominal_symbol_loose -- transitional bridging helper
# ---------------------------------------------------------------------------

class TestLooseBridging:
    def test_bare_matches_qname_bearing(self) -> None:
        bare = NominalType("Foo")
        qn = NominalType("Foo", _module_qname="mod.Foo")
        assert same_nominal_symbol_loose(bare, qn)
        assert same_nominal_symbol_loose(qn, bare)

    def test_different_qnames_still_unequal(self) -> None:
        # loose must NOT paper over cross-module collisions.
        a = NominalType("Foo", _module_qname="pkg_a.Foo")
        b = NominalType("Foo", _module_qname="pkg_b.Foo")
        assert not same_nominal_symbol_loose(a, b)

    def test_rejects_structural_differences(self) -> None:
        # Different name / type_args / is_protocol -> False regardless
        # of qname state.
        bare_a = NominalType("Foo")
        bare_b = NominalType("Bar")
        assert not same_nominal_symbol_loose(bare_a, bare_b)


# ---------------------------------------------------------------------------
# 3. Qname-aware lookup on TypeRegistry
# ---------------------------------------------------------------------------

class TestRegistryLookup:
    def _make_registry_with_shadow(self) -> tuple[TypeRegistry, RecordInfo, RecordInfo]:
        """A registry where short name "Foo" is shadowed:
        - `__main__.Foo` registered via `register_record` (local).
        - `pkg.Foo` registered via `register_module` (import-only).
        """
        reg = TypeRegistry()
        main_foo = RecordInfo(name="Foo", fields=[], module="__main__")
        pkg_foo = RecordInfo(name="Foo", fields=[], module="pkg")
        reg.register_record(main_foo)
        reg.register_module(ModuleInfo(name="pkg", records={"Foo": pkg_foo}))
        _attach_record_td(main_foo)
        _attach_record_td(pkg_foo)
        return reg, main_foo, pkg_foo

    def test_qname_routes_to_cross_module_record(self) -> None:
        reg, main_foo, pkg_foo = self._make_registry_with_shadow()
        n = NominalType("Foo", _module_qname="pkg.Foo")
        assert reg.get_record_for_type(n) is pkg_foo

    def test_qname_routes_to_local_record(self) -> None:
        reg, main_foo, pkg_foo = self._make_registry_with_shadow()
        n = NominalType("Foo", _module_qname="__main__.Foo")
        assert reg.get_record_for_type(n) is main_foo

    def test_bare_falls_back_to_short_name(self) -> None:
        reg, main_foo, pkg_foo = self._make_registry_with_shadow()
        # Bare placeholder still resolves to whatever is in
        # `self.records[name]` (here: the local __main__.Foo).  This
        # is the transient parse/resolve behavior; callers that need
        # stricter semantics should pass a qname-bearing NominalType.
        n = NominalType("Foo")
        assert reg.get_record_for_type(n) is main_foo

    def test_is_subclass_of_cross_module(self) -> None:
        # Local `Base` shadows `pkg.Base`, and `pkg.Child` extends
        # `pkg.Base`.  Qname-aware lookup must walk the pkg chain
        # rather than the locally-registered `Base`.
        reg = TypeRegistry()
        main_base = RecordInfo(name="Base", fields=[], module="__main__")
        reg.register_record(main_base)
        _attach_record_td(main_base)

        pkg_base = RecordInfo(name="Base", fields=[], module="pkg")
        pkg_child = RecordInfo(
            name="Child", fields=[], module="pkg",
            parent=NominalType("Base", _module_qname="pkg.Base"),
        )
        reg.register_module(ModuleInfo(
            name="pkg", records={"Base": pkg_base, "Child": pkg_child},
        ))
        _attach_record_td(pkg_base)
        _attach_record_td(pkg_child)

        child_n = NominalType("Child", _module_qname="pkg.Child")
        base_n = NominalType("Base", _module_qname="pkg.Base")
        assert reg.is_subclass_of(child_n, base_n)

        # Sanity: local __main__.Base is NOT a parent of pkg.Child.
        main_base_n = NominalType("Base", _module_qname="__main__.Base")
        assert not reg.is_subclass_of(child_n, main_base_n)
