"""Lowering threads a frontend-IR `Record.base` into `TpyRecord.bases`, so a
plugin-emitted subclass inherits the base's fields/methods the same way a
parser-produced `class D(B)` does.
"""

from tpyc.frontend_ir.lower import lower_module
from tpyc.frontend_ir.nodes import (
    Field, FrontendModule, NamedType, Record)


def _lower_records(*records: Record):
    fm = FrontendModule(qname="testmod", records=records)
    return lower_module(fm, "testplugin")


def _by_name(module, name: str):
    return next(r for r in module.records if r.name == name)


def test_record_base_threads_into_bases():
    base = Record(name="Base", fields=(
        Field(name="Id", type=NamedType(name="Int32")),))
    derived = Record(name="Derived", base=NamedType(name="Base"), fields=(
        Field(name="Extra", type=NamedType(name="Int32")),))
    res = _lower_records(base, derived)
    assert res.module is not None
    d = _by_name(res.module, "Derived")
    assert len(d.bases) == 1
    assert getattr(d.bases[0], "name", None) == "Base"


def test_record_without_base_has_no_bases():
    res = _lower_records(Record(name="Plain", fields=(
        Field(name="X", type=NamedType(name="Int32")),)))
    assert res.module is not None
    assert _by_name(res.module, "Plain").bases == []


def test_record_with_unlowerable_base_emits_diag():
    # A base whose type can't be lowered makes `_lower_record` bail (return
    # None, None), aborting the lowering with an IR-invalid diagnostic -- the
    # failure surfaces rather than being swallowed into an empty `bases`.
    bad = Record(name="Bad", base=NamedType(name="Base", args=(object(),)),
                 fields=(Field(name="X", type=NamedType(name="Int32")),))
    res = _lower_records(bad)
    assert res.module is None
    assert res.diagnostics
