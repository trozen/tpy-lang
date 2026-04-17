"""
Conformance tests for the TypeDef registry (Phase A gate).

For every qname registered in `type_def_registry._type_defs`, instantiate
the corresponding existing subclass with canonical args and assert that
the TypeDef's intrinsic fields agree with the subclass overrides.

Once this is green, Phase B can delete subclasses one at a time with
confidence that sema/codegen reading from TypeDef get the same answers
they used to get from the subclass methods.
"""

from __future__ import annotations

import pytest

from tpyc import typesys as ts
from tpyc.type_def_registry import (
    TypeCategory,
    get_type_def,
    type_def_of,
    resolve_send_sync,
    _type_defs,
)


# Map qname -> a concrete TpyType instance with canonical args.
# Primitives resolve to the typesys singleton. Containers get Int32 /
# (str, Int32) / (Int32, 10) as their type args.
def _canonical_instances() -> dict[str, ts.TpyType]:
    I32 = ts.INT32
    STR = ts.STR
    cases: dict[str, ts.TpyType] = {
        "tpy.Int8":  ts.INT8,  "tpy.Int16": ts.INT16,
        "tpy.Int32": ts.INT32, "tpy.Int64": ts.INT64,
        "tpy.UInt8":  ts.UINT8,  "tpy.UInt16": ts.UINT16,
        "tpy.UInt32": ts.UINT32, "tpy.UInt64": ts.UINT64,
        "builtins.int":   ts.BIGINT,
        "builtins.float": ts.FLOAT,
        "tpy.Float32":    ts.FLOAT32,
        "builtins.bool":  ts.BOOL,
        "tpy.Char":       ts.CHAR,
        "builtins.str":   ts.STR,
        "tpy.String":     ts.STRING,
        "tpy.StrView":    ts.STRVIEW,
        "tpy.FStr":       ts.FSTR,
        "builtins.bytes":     ts.BYTES,
        "builtins.bytearray": ts.BYTEARRAY,
        "tpy.BytesView":      ts.BYTESVIEW,
        "builtins.basic_slice": ts.BASIC_SLICE,
        "builtins.slice":       ts.SLICE,
        "builtins.list":  ts.make_list(I32),
        "builtins.dict":  ts.make_dict(STR, I32),
        "builtins.set":   ts.make_set(I32),
        "builtins.dict_keys":   ts.make_dict_keys_view(STR, I32),
        "builtins.dict_values": ts.make_dict_values_view(STR, I32),
        "builtins.dict_items":  ts.make_dict_items_view(STR, I32),
        "builtins.Range":  ts.RangeType(I32),
        "tpy.Array":    ts.make_array(I32, 10),
        "tpy.Span":     ts.make_span(I32),
        "tpy.SpanIter": ts.make_span_iter(I32),
        "tpy.CopyIter": ts.make_copy_iter(I32),
        "tpy.OwnIter":  ts.make_own_iter(I32),
    }
    return cases


@pytest.fixture(scope="module")
def instances() -> dict[str, ts.TpyType]:
    return _canonical_instances()


def test_every_registered_qname_has_a_canonical_instance(instances):
    """Every qname in the registry must have a canonical instance in the
    test so the conformance assertions actually cover it."""
    missing = set(_type_defs) - set(instances)
    assert not missing, f"Registry qnames without canonical instance: {missing}"


def test_type_def_of_matches_registry(instances):
    for qname, inst in instances.items():
        td = type_def_of(inst)
        assert td is not None, f"type_def_of returned None for {qname}"
        assert td.qname == qname, (
            f"qname mismatch: instance.qualified_name()={inst.qualified_name()!r} "
            f"lookup returned TypeDef({td.qname!r})"
        )


def test_is_value_type_matches_subclass(instances):
    for qname, inst in instances.items():
        td = get_type_def(qname)
        assert td is not None
        assert td.is_value_type == inst.is_value_type(), (
            f"{qname}: TypeDef.is_value_type={td.is_value_type} "
            f"but instance.is_value_type()={inst.is_value_type()}"
        )


def test_subscript_borrows_matches_subclass(instances):
    for qname, inst in instances.items():
        td = get_type_def(qname)
        assert td is not None
        assert td.subscript_borrows == inst.subscript_borrows(), (
            f"{qname}: TypeDef.subscript_borrows={td.subscript_borrows} "
            f"but instance.subscript_borrows()={inst.subscript_borrows()}"
        )


def test_is_send_override_matches_subclass(instances):
    for qname, inst in instances.items():
        td = get_type_def(qname)
        assert td is not None
        if td.is_send is not None:
            resolved = resolve_send_sync(td.is_send, inst.type_args)
            assert resolved == inst.is_send(), (
                f"{qname}: TypeDef.is_send resolved={resolved} but "
                f"instance.is_send()={inst.is_send()}"
            )


def test_is_sync_override_matches_subclass(instances):
    for qname, inst in instances.items():
        td = get_type_def(qname)
        assert td is not None
        if td.is_sync is not None:
            resolved = resolve_send_sync(td.is_sync, inst.type_args)
            assert resolved == inst.is_sync(), (
                f"{qname}: TypeDef.is_sync resolved={resolved} but "
                f"instance.is_sync()={inst.is_sync()}"
            )


def test_cpp_formatter_matches_subclass(instances):
    for qname, inst in instances.items():
        td = get_type_def(qname)
        assert td is not None
        if td.cpp_formatter is not None:
            assert td.cpp_formatter(inst.type_args) == inst.to_cpp(), (
                f"{qname}: TypeDef.cpp_formatter({inst.type_args!r})="
                f"{td.cpp_formatter(inst.type_args)!r} but "
                f"instance.to_cpp()={inst.to_cpp()!r}"
            )


def test_predicate_functions_agree_with_isinstance(instances):
    from tpyc.type_def_registry import (
        is_list, is_dict, is_set, is_array, is_span, is_dict_view,
        is_range, is_iterator_adapter,
    )
    # RangeType still has a dedicated subclass; list is now plain NominalType
    checks = [
        (is_range,            ts.RangeType),
    ]
    for pred, cls in checks:
        for qname, inst in instances.items():
            assert pred(inst) == isinstance(inst, cls), (
                f"{pred.__name__}({qname}) = {pred(inst)} but "
                f"isinstance(..., {cls.__name__}) = {isinstance(inst, cls)}"
            )
    # set / dict / dict views: predicates keyed on qname (no dedicated subclass)
    for qname, inst in instances.items():
        assert is_set(inst) == (qname == "builtins.set"), (
            f"is_set({qname}) = {is_set(inst)}"
        )
        assert is_dict(inst) == (qname == "builtins.dict"), (
            f"is_dict({qname}) = {is_dict(inst)}"
        )
        assert is_array(inst) == (qname == "tpy.Array"), (
            f"is_array({qname}) = {is_array(inst)}"
        )
        assert is_span(inst) == (qname == "tpy.Span"), (
            f"is_span({qname}) = {is_span(inst)}"
        )
        assert is_list(inst) == (qname == "builtins.list"), (
            f"is_list({qname}) = {is_list(inst)}"
        )
        expected = qname in ("builtins.dict_keys", "builtins.dict_values", "builtins.dict_items")
        assert is_dict_view(inst) == expected, (
            f"is_dict_view({qname}) = {is_dict_view(inst)} but expected {expected}"
        )
    # iterator adapters: SpanIter + CopyIter + OwnIter (qname-based check)
    for qname, inst in instances.items():
        expected = qname in ("tpy.SpanIter", "tpy.CopyIter", "tpy.OwnIter")
        assert is_iterator_adapter(inst) == expected, (
            f"is_iterator_adapter({qname}) = {is_iterator_adapter(inst)} but expected {expected}"
        )


def test_pending_types_are_not_concrete_containers():
    """PendingListType / PendingDictType / PendingSetType share builtin qnames
    with their resolved form ("builtins.list" etc.) but are distinct TpyType
    subclasses with their own fields. `is_list` / `is_dict` / `is_set` must
    reject them so call sites that access `type_args[0]` don't crash. Regression
    test for the `list({})` crash (PendingDictType passing `is_dict`).
    """
    from tpyc.type_def_registry import is_list, is_dict, is_set
    pending_list = ts.PendingListType(ts.INT32, 0, 0)
    pending_dict = ts.PendingDictType(ts.STR, ts.INT32, 0)
    pending_set = ts.PendingSetType(ts.INT32, 0)

    # qnames match their resolved form (intentional) ...
    assert pending_list.qualified_name() == "builtins.list"
    assert pending_dict.qualified_name() == "builtins.dict"
    assert pending_set.qualified_name() == "builtins.set"

    # ... but the predicates must still reject them
    assert not is_list(pending_list), "is_list must reject PendingListType"
    assert not is_dict(pending_dict), "is_dict must reject PendingDictType"
    assert not is_set(pending_set),   "is_set must reject PendingSetType"
