"""Tests for `tpyc.module_names.module_from_qname`.

Drives `#include` emission via the reached-modules set in
`tpyc/sema/reach_analysis.py` and SCC peer detection in
`tpyc/compiler.py`. Must not prefix-walk the qname of a record whose
defining file diverges from its qname (e.g. `Poll` -- qname
`tpy.coro.Poll`, body in `tpy/_core/_types.py`).
"""

from __future__ import annotations

from tpyc import qnames, typesys as ts
from tpyc.module_names import module_from_qname


def _registry_with_modules(names: list[str], builtin: list[str] | None = None) -> ts.TypeRegistry:
    registry = ts.TypeRegistry()
    builtin_set = set(builtin or [])
    for name in names:
        registry.register_module(
            ts.ModuleInfo(name=name, is_builtin=name in builtin_set)
        )
    return registry


def test_record_with_divergent_qname_uses_record_module():
    registry = _registry_with_modules(["tpy", "tpy.coro", "tpy._core._types"])
    record = ts.RecordInfo(
        name="Poll",
        module="tpy",
        defining_module="tpy._core._types",
        builtin_type_key=qnames.POLL,
        fields=[],
        methods={},
    )
    registry.register_record(record)

    assert module_from_qname(qnames.POLL, registry) == "tpy"


def test_record_with_aligned_qname_still_returns_module():
    registry = _registry_with_modules(["pkg", "pkg.sub"])
    record = ts.RecordInfo(
        name="Widget",
        module="pkg.sub",
        defining_module="pkg.sub",
        builtin_type_key="pkg.sub.Widget",
        fields=[],
        methods={},
    )
    registry.register_record(record)

    assert module_from_qname("pkg.sub.Widget", registry) == "pkg.sub"


def test_unregistered_qname_falls_back_to_prefix_walk():
    registry = _registry_with_modules(["pkg", "pkg.sub"])

    assert module_from_qname("pkg.sub.Outer.Inner", registry) == "pkg.sub"
    assert module_from_qname("pkg.Foo", registry) == "pkg"


def test_unregistered_qname_with_no_module_ancestor_falls_to_first_component():
    registry = _registry_with_modules([])

    assert module_from_qname("foo.Bar", registry) == "foo"


def test_single_component_qname_returns_none():
    registry = _registry_with_modules([])

    assert module_from_qname("BareName", registry) is None
