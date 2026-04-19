"""
Conformance tests for the TypeDef registry.

Two layers:

1. Per-qname conformance (Phase A gate): for each qname in the registry,
   assert TypeDef fields agree with what the corresponding TpyType
   instance returns. Catches "registry forgot about X" before the
   subclass is deleted. Once the subclass is gone, the instance and
   registry read from the same source and the check is tautological --
   but it costs nothing to keep.

2. Primitive hard-coded snapshot (Phase D step 0): an explicit golden
   table of every primitive's intrinsic per-qname behavior, compared
   against the live instance. This is *independent* validation --
   hard-coded answers that remain valid after the primitive subclass is
   deleted and the instance starts reading from the registry. The
   snapshot pins what Phase D must preserve.
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
from tpyc.type_def_registry import IntTraits, FloatTraits


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
            args = inst.type_args
            assert td.cpp_formatter(args) == inst.to_cpp(), (
                f"{qname}: TypeDef.cpp_formatter({args!r})="
                f"{td.cpp_formatter(args)!r} but "
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


# =========================================================================
# Primitive hard-coded snapshot (Phase D step 0).
#
# Captures intrinsic per-qname behavior of every primitive currently
# implemented as a dedicated TpyType subclass. The snapshot is the golden
# reference for Phase D: as the subclasses are collapsed into NominalType +
# TypeDef, the instance methods start reading from the registry, but the
# hard-coded expected values here stay unchanged -- which makes this an
# independent validation that Phase D didn't drift behavior.
#
# Keep this table hand-written. Regenerating it from the instances would
# defeat the whole point (the check would pass by definition).
# =========================================================================


# Sentinel -- FStr.to_cpp() raises TypeError (compile-time only type).
_RAISES_TYPE_ERROR = object()


# Each entry pins the answers expected from the live TpyType instance *and*,
# eventually, from the TypeDef registry when the primitive becomes a plain
# NominalType. Fields mirror the current subclass overrides:
#
#   category                     -- TypeCategory enum value
#   is_value_type                -- bool
#   is_send / is_sync            -- bool (default: == is_value_type)
#   subscript_borrows            -- bool (primitives: always False today)
#   is_expensive_copy            -- bool
#   param_needs_copy_for_reassign-- bool
#   is_compile_time_only         -- bool
#   to_cpp                       -- str or _RAISES_TYPE_ERROR
#   to_cpp_param_type            -- str (default: == to_cpp for value types)
#   element_qname                -- str | None (qname of get_element_type())
#   int_bits / int_signed        -- only for FIXED_INT category
#   float_bits                   -- only for FLOAT category

PRIMITIVE_SNAPSHOT: dict[str, dict] = {
    # --- Fixed-width integers --------------------------------------------
    "tpy.Int8":  dict(category=TypeCategory.FIXED_INT, is_value_type=True,
                      is_send=True, is_sync=True, subscript_borrows=False,
                      is_expensive_copy=False, param_needs_copy_for_reassign=False,
                      is_compile_time_only=False,
                      to_cpp="int8_t", to_cpp_param_type="int8_t",
                      element_qname=None, int_bits=8,  int_signed=True),
    "tpy.Int16": dict(category=TypeCategory.FIXED_INT, is_value_type=True,
                      is_send=True, is_sync=True, subscript_borrows=False,
                      is_expensive_copy=False, param_needs_copy_for_reassign=False,
                      is_compile_time_only=False,
                      to_cpp="int16_t", to_cpp_param_type="int16_t",
                      element_qname=None, int_bits=16, int_signed=True),
    "tpy.Int32": dict(category=TypeCategory.FIXED_INT, is_value_type=True,
                      is_send=True, is_sync=True, subscript_borrows=False,
                      is_expensive_copy=False, param_needs_copy_for_reassign=False,
                      is_compile_time_only=False,
                      to_cpp="int32_t", to_cpp_param_type="int32_t",
                      element_qname=None, int_bits=32, int_signed=True),
    "tpy.Int64": dict(category=TypeCategory.FIXED_INT, is_value_type=True,
                      is_send=True, is_sync=True, subscript_borrows=False,
                      is_expensive_copy=False, param_needs_copy_for_reassign=False,
                      is_compile_time_only=False,
                      to_cpp="int64_t", to_cpp_param_type="int64_t",
                      element_qname=None, int_bits=64, int_signed=True),
    "tpy.UInt8":  dict(category=TypeCategory.FIXED_INT, is_value_type=True,
                       is_send=True, is_sync=True, subscript_borrows=False,
                       is_expensive_copy=False, param_needs_copy_for_reassign=False,
                       is_compile_time_only=False,
                       to_cpp="uint8_t", to_cpp_param_type="uint8_t",
                       element_qname=None, int_bits=8,  int_signed=False),
    "tpy.UInt16": dict(category=TypeCategory.FIXED_INT, is_value_type=True,
                       is_send=True, is_sync=True, subscript_borrows=False,
                       is_expensive_copy=False, param_needs_copy_for_reassign=False,
                       is_compile_time_only=False,
                       to_cpp="uint16_t", to_cpp_param_type="uint16_t",
                       element_qname=None, int_bits=16, int_signed=False),
    "tpy.UInt32": dict(category=TypeCategory.FIXED_INT, is_value_type=True,
                       is_send=True, is_sync=True, subscript_borrows=False,
                       is_expensive_copy=False, param_needs_copy_for_reassign=False,
                       is_compile_time_only=False,
                       to_cpp="uint32_t", to_cpp_param_type="uint32_t",
                       element_qname=None, int_bits=32, int_signed=False),
    "tpy.UInt64": dict(category=TypeCategory.FIXED_INT, is_value_type=True,
                       is_send=True, is_sync=True, subscript_borrows=False,
                       is_expensive_copy=False, param_needs_copy_for_reassign=False,
                       is_compile_time_only=False,
                       to_cpp="uint64_t", to_cpp_param_type="uint64_t",
                       element_qname=None, int_bits=64, int_signed=False),

    # --- Big int ---------------------------------------------------------
    "builtins.int": dict(category=TypeCategory.BIG_INT, is_value_type=True,
                         is_send=True, is_sync=True, subscript_borrows=False,
                         is_expensive_copy=True, param_needs_copy_for_reassign=True,
                         is_compile_time_only=False,
                         to_cpp="::tpy::BigInt",
                         to_cpp_param_type="const ::tpy::BigInt&",
                         element_qname=None),

    # --- Floats ----------------------------------------------------------
    "builtins.float": dict(category=TypeCategory.FLOAT, is_value_type=True,
                           is_send=True, is_sync=True, subscript_borrows=False,
                           is_expensive_copy=False, param_needs_copy_for_reassign=False,
                           is_compile_time_only=False,
                           to_cpp="double", to_cpp_param_type="double",
                           element_qname=None, float_bits=64),
    "tpy.Float32":    dict(category=TypeCategory.FLOAT, is_value_type=True,
                           is_send=True, is_sync=True, subscript_borrows=False,
                           is_expensive_copy=False, param_needs_copy_for_reassign=False,
                           is_compile_time_only=False,
                           to_cpp="float", to_cpp_param_type="float",
                           element_qname=None, float_bits=32),

    # --- Bool / Char -----------------------------------------------------
    "builtins.bool": dict(category=TypeCategory.BOOL, is_value_type=True,
                          is_send=True, is_sync=True, subscript_borrows=False,
                          is_expensive_copy=False, param_needs_copy_for_reassign=False,
                          is_compile_time_only=False,
                          to_cpp="bool", to_cpp_param_type="bool",
                          element_qname=None),
    "tpy.Char":      dict(category=TypeCategory.CHAR, is_value_type=True,
                          is_send=True, is_sync=True, subscript_borrows=False,
                          is_expensive_copy=False, param_needs_copy_for_reassign=False,
                          is_compile_time_only=False,
                          to_cpp="char", to_cpp_param_type="char",
                          element_qname=None),

    # --- String family ---------------------------------------------------
    "builtins.str": dict(category=TypeCategory.STR, is_value_type=True,
                         is_send=True, is_sync=True, subscript_borrows=False,
                         is_expensive_copy=True, param_needs_copy_for_reassign=True,
                         is_compile_time_only=False,
                         to_cpp="std::string",
                         to_cpp_param_type="std::string_view",
                         element_qname="tpy.Char"),
    "tpy.String":   dict(category=TypeCategory.STR, is_value_type=True,
                         is_send=True, is_sync=True, subscript_borrows=False,
                         is_expensive_copy=True, param_needs_copy_for_reassign=True,
                         is_compile_time_only=False,
                         to_cpp="std::string",
                         to_cpp_param_type="const std::string&",
                         element_qname="tpy.Char"),
    "tpy.StrView":  dict(category=TypeCategory.STR, is_value_type=True,
                         is_send=False, is_sync=True, subscript_borrows=False,
                         is_expensive_copy=False, param_needs_copy_for_reassign=False,
                         is_compile_time_only=False,
                         to_cpp="std::string_view",
                         to_cpp_param_type="std::string_view",
                         element_qname="tpy.Char"),
    "tpy.FStr":     dict(category=TypeCategory.STR, is_value_type=True,
                         is_send=True, is_sync=True, subscript_borrows=False,
                         is_expensive_copy=False, param_needs_copy_for_reassign=False,
                         is_compile_time_only=True,
                         to_cpp=_RAISES_TYPE_ERROR, to_cpp_param_type=_RAISES_TYPE_ERROR,
                         element_qname=None),

    # --- Bytes family ----------------------------------------------------
    "builtins.bytes":     dict(category=TypeCategory.BYTES, is_value_type=True,
                               is_send=True, is_sync=True, subscript_borrows=False,
                               is_expensive_copy=True, param_needs_copy_for_reassign=True,
                               is_compile_time_only=False,
                               to_cpp="std::vector<uint8_t>",
                               to_cpp_param_type="std::span<const uint8_t>",
                               element_qname="tpy.UInt8"),
    "builtins.bytearray": dict(category=TypeCategory.BYTES, is_value_type=True,
                               is_send=True, is_sync=True, subscript_borrows=False,
                               is_expensive_copy=True, param_needs_copy_for_reassign=True,
                               is_compile_time_only=False,
                               to_cpp="std::vector<uint8_t>",
                               to_cpp_param_type="const std::vector<uint8_t>&",
                               element_qname="tpy.UInt8"),
    "tpy.BytesView":      dict(category=TypeCategory.BYTES, is_value_type=True,
                               is_send=False, is_sync=True, subscript_borrows=False,
                               is_expensive_copy=False, param_needs_copy_for_reassign=False,
                               is_compile_time_only=False,
                               to_cpp="std::span<const uint8_t>",
                               to_cpp_param_type="std::span<const uint8_t>",
                               element_qname="tpy.UInt8"),

    # --- Slices ----------------------------------------------------------
    "builtins.basic_slice": dict(category=TypeCategory.SLICE, is_value_type=True,
                                 is_send=True, is_sync=True, subscript_borrows=False,
                                 is_expensive_copy=False, param_needs_copy_for_reassign=False,
                                 is_compile_time_only=False,
                                 to_cpp="::tpy::BasicSlice",
                                 to_cpp_param_type="::tpy::BasicSlice",
                                 element_qname=None),
    "builtins.slice":       dict(category=TypeCategory.SLICE, is_value_type=True,
                                 is_send=True, is_sync=True, subscript_borrows=False,
                                 is_expensive_copy=False, param_needs_copy_for_reassign=False,
                                 is_compile_time_only=False,
                                 to_cpp="::tpy::Slice",
                                 to_cpp_param_type="::tpy::Slice",
                                 element_qname=None),
}


_PRIMITIVE_INSTANCES: dict[str, ts.TpyType] = {
    "tpy.Int8":  ts.INT8,  "tpy.Int16":  ts.INT16,
    "tpy.Int32": ts.INT32, "tpy.Int64":  ts.INT64,
    "tpy.UInt8": ts.UINT8, "tpy.UInt16": ts.UINT16,
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
}


def test_primitive_snapshot_covers_every_registered_primitive():
    """Every registry entry in a primitive-ish category must appear in the
    snapshot. If a new primitive qname gets registered without being added
    here, this test fails and the author must decide whether the new qname
    is primitive (add to snapshot) or not (expand this ignore list)."""
    primitive_categories = {
        TypeCategory.FIXED_INT, TypeCategory.BIG_INT, TypeCategory.FLOAT,
        TypeCategory.BOOL, TypeCategory.CHAR, TypeCategory.STR,
        TypeCategory.BYTES, TypeCategory.SLICE,
    }
    registered_primitives = {
        qn for qn, td in _type_defs.items() if td.category in primitive_categories
    }
    missing = registered_primitives - set(PRIMITIVE_SNAPSHOT)
    assert not missing, (
        f"Registered primitive qnames without a snapshot entry: {missing}. "
        f"Add them to PRIMITIVE_SNAPSHOT with their expected per-qname behavior."
    )
    extra = set(PRIMITIVE_SNAPSHOT) - registered_primitives
    assert not extra, (
        f"PRIMITIVE_SNAPSHOT entries that aren't registered primitives: {extra}."
    )


@pytest.mark.parametrize("qname", sorted(PRIMITIVE_SNAPSHOT))
def test_primitive_instance_matches_snapshot(qname):
    """Hard-coded golden snapshot of every primitive's intrinsic behavior.
    Validated against the live TpyType instance. Must keep passing through
    Phase D even as the primitive subclasses collapse into NominalType."""
    expected = PRIMITIVE_SNAPSHOT[qname]
    inst = _PRIMITIVE_INSTANCES[qname]

    # qualified_name must round-trip.
    assert inst.qualified_name() == qname

    # category comes from the TypeDef registry (by construction today,
    # still true after migration). Included for completeness -- if the
    # category ever drifts this catches it.
    td = get_type_def(qname)
    assert td is not None
    assert td.category is expected["category"], (
        f"{qname}: TypeDef.category={td.category!r} "
        f"but snapshot expects {expected['category']!r}"
    )

    assert inst.is_value_type() == expected["is_value_type"], qname
    assert inst.is_send()       == expected["is_send"],       qname
    assert inst.is_sync()       == expected["is_sync"],       qname
    assert inst.subscript_borrows() == expected["subscript_borrows"], qname
    assert inst.is_expensive_copy() == expected["is_expensive_copy"], qname
    assert inst.param_needs_copy_for_reassign() == expected["param_needs_copy_for_reassign"], qname
    assert inst.is_compile_time_only() == expected["is_compile_time_only"], qname

    if expected["to_cpp"] is _RAISES_TYPE_ERROR:
        with pytest.raises(TypeError):
            inst.to_cpp()
    else:
        assert inst.to_cpp() == expected["to_cpp"], qname

    if expected["to_cpp_param_type"] is _RAISES_TYPE_ERROR:
        with pytest.raises(TypeError):
            inst.to_cpp_param_type()
    else:
        assert inst.to_cpp_param_type() == expected["to_cpp_param_type"], qname

    elem = inst.get_element_type()
    if expected["element_qname"] is None:
        assert elem is None, f"{qname}: expected no element, got {elem!r}"
    else:
        assert elem is not None, f"{qname}: expected element, got None"
        assert elem.qualified_name() == expected["element_qname"], qname

    # Category-specific checks.
    if expected["category"] is TypeCategory.FIXED_INT:
        # Post-Phase-D: bits/signed live in TypeDef.int_traits, reached via
        # int_traits_of(inst). Pin the accessor so Phase D can't silently
        # break the lookup for any caller.
        from tpyc.type_def_registry import int_traits_of
        tr = int_traits_of(inst)
        assert tr is not None, qname
        assert tr.bits == expected["int_bits"], qname
        assert tr.signed == expected["int_signed"], qname


@pytest.mark.parametrize("qname", sorted(PRIMITIVE_SNAPSHOT))
def test_primitive_type_def_matches_snapshot(qname):
    """The TypeDef registry entry for each primitive qname must carry the
    snapshot values directly (independent of any TpyType subclass). This
    is the forward-facing half of Phase D validation: Step 1 enriches
    TypeDef with these fields; later steps flip callers to read them. The
    subclass check above and this check both compare against the same
    PRIMITIVE_SNAPSHOT, so if a caller reads TypeDef and drifts from
    observed behavior the pair of tests catches it."""
    expected = PRIMITIVE_SNAPSHOT[qname]
    td = get_type_def(qname)
    assert td is not None, qname
    assert td.category is expected["category"], qname
    assert td.is_value_type == expected["is_value_type"], qname
    assert td.subscript_borrows == expected["subscript_borrows"], qname
    assert td.is_expensive_copy == expected["is_expensive_copy"], qname
    assert td.param_needs_copy_for_reassign == expected["param_needs_copy_for_reassign"], qname
    assert td.is_compile_time_only == expected["is_compile_time_only"], qname

    # is_send / is_sync: default None means "follow is_value_type". Resolve
    # against empty type_args (primitives have none) and compare.
    send_resolved = resolve_send_sync(td.is_send, ())
    expected_send = expected["is_send"]
    if send_resolved is None:
        assert td.is_value_type == expected_send, qname
    else:
        assert send_resolved == expected_send, qname
    sync_resolved = resolve_send_sync(td.is_sync, ())
    expected_sync = expected["is_sync"]
    if sync_resolved is None:
        assert td.is_value_type == expected_sync, qname
    else:
        assert sync_resolved == expected_sync, qname

    # cpp_formatter: populated for every primitive except FStr (compile-
    # time only -- its to_cpp raises TypeError, which is an absence of
    # formatter rather than a concrete string).
    if expected["to_cpp"] is _RAISES_TYPE_ERROR:
        assert td.cpp_formatter is None, qname
    else:
        assert td.cpp_formatter is not None, qname
        assert td.cpp_formatter(()) == expected["to_cpp"], qname

    if expected["to_cpp_param_type"] is _RAISES_TYPE_ERROR:
        assert td.param_cpp_formatter is None, qname
    else:
        assert td.param_cpp_formatter is not None, qname
        assert td.param_cpp_formatter(()) == expected["to_cpp_param_type"], qname

    if expected["category"] is TypeCategory.FIXED_INT:
        assert td.int_traits is not None, qname
        assert td.int_traits.bits == expected["int_bits"], qname
        assert td.int_traits.signed == expected["int_signed"], qname
    else:
        assert td.int_traits is None, qname

    if expected["category"] is TypeCategory.FLOAT:
        assert td.float_traits is not None, qname
        assert td.float_traits.bits == expected["float_bits"], qname
    else:
        assert td.float_traits is None, qname


def test_primitive_predicates_match_isinstance():
    """Every primitive predicate returns True for exactly one canonical qname
    (or, for category predicates like is_fixed_int_type, for exactly the
    expected set of qnames). Post-Phase D the primitive subclasses are gone,
    so the historical isinstance arm of this test is empty; the qname-based
    checks below are what pins predicate correctness today."""
    from tpyc.type_def_registry import (
        is_fixed_int_type, is_big_int_type, is_bool_type, is_char_type,
        is_float_category, is_str_category, is_bytes_category, is_slice_category,
        is_str_type, is_string_type, is_str_view_type, is_fstr_type,
        is_float64_type, is_float32_type,
        is_bytes_type, is_bytearray_type, is_bytes_view_type,
    )
    # After Phase D step 6: all primitive subclasses are NominalType singletons.
    # Validate the qname-only predicates directly (no isinstance-equivalent).
    qname_preds = {
        is_fstr_type:        "tpy.FStr",
        is_str_type:         "builtins.str",
        is_string_type:      "tpy.String",
        is_str_view_type:    "tpy.StrView",
        is_bytes_type:       "builtins.bytes",
        is_bytearray_type:   "builtins.bytearray",
        is_bytes_view_type:  "tpy.BytesView",
        is_bool_type:        "builtins.bool",
        is_char_type:        "tpy.Char",
        is_float64_type:     "builtins.float",
        is_float32_type:     "tpy.Float32",
        is_big_int_type:     "builtins.int",
    }
    # Fixed-int qnames span Int8..UInt64; is_fixed_int_type is a category
    # predicate, so check it membership-style instead of qname-equality.
    fixed_int_qnames = {
        "tpy.Int8", "tpy.Int16", "tpy.Int32", "tpy.Int64",
        "tpy.UInt8", "tpy.UInt16", "tpy.UInt32", "tpy.UInt64",
    }
    for qname, inst in _PRIMITIVE_INSTANCES.items():
        assert is_fixed_int_type(inst) == (qname in fixed_int_qnames), (
            f"is_fixed_int_type({qname}) = {is_fixed_int_type(inst)}"
        )
    for pred, expected_qname in qname_preds.items():
        for qname, inst in _PRIMITIVE_INSTANCES.items():
            assert pred(inst) == (qname == expected_qname), (
                f"{pred.__name__}({qname}) = {pred(inst)}"
            )

    # Category predicates: verify against snapshot category.
    cat_preds = {
        TypeCategory.FIXED_INT: is_fixed_int_type,
        TypeCategory.BIG_INT:   is_big_int_type,
        TypeCategory.BOOL:      is_bool_type,
        TypeCategory.CHAR:      is_char_type,
        TypeCategory.FLOAT:     is_float_category,
        TypeCategory.STR:       is_str_category,
        TypeCategory.BYTES:     is_bytes_category,
        TypeCategory.SLICE:     is_slice_category,
    }
    for qname, expected in PRIMITIVE_SNAPSHOT.items():
        inst = _PRIMITIVE_INSTANCES[qname]
        for cat, pred in cat_preds.items():
            assert pred(inst) == (expected["category"] is cat), (
                f"{pred.__name__}({qname}) = {pred(inst)} but "
                f"category is {expected['category']}"
            )


def test_trait_accessors_match_snapshot():
    """int_traits_of / float_traits_of must return the expected traits for
    category-appropriate primitives, None otherwise."""
    from tpyc.type_def_registry import int_traits_of, float_traits_of
    for qname, expected in PRIMITIVE_SNAPSHOT.items():
        inst = _PRIMITIVE_INSTANCES[qname]
        int_tr = int_traits_of(inst)
        float_tr = float_traits_of(inst)
        if expected["category"] is TypeCategory.FIXED_INT:
            assert int_tr is not None
            assert int_tr.bits == expected["int_bits"], qname
            assert int_tr.signed == expected["int_signed"], qname
        else:
            assert int_tr is None, qname
        if expected["category"] is TypeCategory.FLOAT:
            assert float_tr is not None
            assert float_tr.bits == expected["float_bits"], qname
        else:
            assert float_tr is None, qname


def test_is_any_str_type_excludes_fstr():
    """is_any_str_type must not report FStr as a string type even though FStr
    shares TypeCategory.STR with the runtime str family in the registry.
    FStr is compile-time only and is never a valid str value at runtime --
    callers relying on is_any_str_type to branch on runtime str types would
    misbehave if FStr slipped in."""
    assert not ts.is_any_str_type(ts.FSTR)
    # Sanity: the legitimate str-family types still report True.
    assert ts.is_any_str_type(ts.STR)
    assert ts.is_any_str_type(ts.STRING)
    assert ts.is_any_str_type(ts.STRVIEW)


def test_fixed_int_range_bounds_match_width():
    """min_value / max_value are derived from (bits, signed). Pin the
    formula against a few representative widths so Phase D can't silently
    change the range computation."""
    cases = [
        ("tpy.Int8",   -2**7,  2**7 - 1),
        ("tpy.Int16",  -2**15, 2**15 - 1),
        ("tpy.Int32",  -2**31, 2**31 - 1),
        ("tpy.Int64",  -2**63, 2**63 - 1),
        ("tpy.UInt8",  0, 2**8 - 1),
        ("tpy.UInt16", 0, 2**16 - 1),
        ("tpy.UInt32", 0, 2**32 - 1),
        ("tpy.UInt64", 0, 2**64 - 1),
    ]
    for qname, lo, hi in cases:
        td = get_type_def(qname)
        assert td is not None and td.int_traits is not None
        assert td.int_traits.min_value == lo, f"{qname}"
        assert td.int_traits.max_value == hi, f"{qname}"


def test_type_matches_numeric_rejects_cross_container():
    """Post-Phase-D all builtin containers (list, set, dict, Array, Span, ...)
    share the NominalType class, so `type(arg) == type(param)` is True for any
    pair of containers. type_matches_numeric must also compare the container
    name -- otherwise resolve_overload could pick a set[Int32] overload for a
    list[Int32] argument via the recursive element-matching branch."""
    from tpyc.sema.overloads import type_matches_numeric
    assert not type_matches_numeric(ts.make_list(ts.INT32), ts.make_set(ts.INT32))
    assert not type_matches_numeric(ts.make_set(ts.INT32), ts.make_list(ts.INT32))
    assert not type_matches_numeric(
        ts.make_dict(ts.STR, ts.INT32),
        ts.make_list(ts.INT32),
    )
    # Positive case: same container with IntLiteral element should still match
    # the concrete-int overload via the element-recursion branch.
    lit = ts.IntLiteralType(value=5)
    assert type_matches_numeric(ts.make_list(lit), ts.make_list(ts.INT32))


def test_structural_match_rejects_cross_container():
    """Same anti-pattern as type_matches_numeric but in _structural_match
    (pass-1 overload resolution). Post-Phase-D list/set/Array/Span are all
    NominalType, so `type(x) != type(y)` returns False and the recursive
    inner-type match would fire -- needs a name comparison too."""
    from tpyc.sema.overloads import _structural_match
    assert not _structural_match(ts.make_list(ts.INT32), ts.make_set(ts.INT32))
    assert not _structural_match(ts.make_set(ts.INT32), ts.make_list(ts.INT32))
    # Positive case: exact match still works.
    assert _structural_match(ts.make_list(ts.INT32), ts.make_list(ts.INT32))
