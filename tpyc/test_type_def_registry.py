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
from tpyc import qnames
from tpyc.typesys import ALL_FIXED_INTS
from tpyc.parse.parser import _FIXED_INT_NAMES as PARSER_FIXED_INT_NAMES
from tpyc.sema.literal_utils import (
    _FIXED_INT_NAMES as LITERAL_UTILS_FIXED_INT_NAMES,
)
from tpyc.macro_api import _FIXED_INT_NAMES as MACRO_FIXED_INT_NAMES
from tpyc.compilation_context import activate_compiler


# Map qname -> a concrete TpyType instance with canonical args.
# Primitives resolve to the typesys singleton. Containers get int32 /
# (str, int32) / (int32, 10) as their type args.
def _canonical_instances() -> dict[str, ts.TpyType]:
    I32 = ts.INT32
    STR = ts.STR
    cases: dict[str, ts.TpyType] = {
        "tpy.int8":  ts.INT8,  "tpy.int16": ts.INT16,
        "tpy.int32": ts.INT32, "tpy.int64": ts.INT64,
        "tpy.uint8":  ts.UINT8,  "tpy.uint16": ts.UINT16,
        "tpy.uint32": ts.UINT32, "tpy.uint64": ts.UINT64,
        "builtins.int":   ts.BIGINT,
        "builtins.float": ts.FLOAT,
        "tpy.float32":    ts.FLOAT32,
        "builtins.bool":  ts.BOOL,
        "tpy.char":       ts.CHAR,
        "builtins.str":   ts.STR,
        "tpy.String":     ts.STRING,
        "tpy.StrView":    ts.STRVIEW,
        "tpy.FStr":       ts.FSTR,
        "builtins.bytes":     ts.BYTES,
        "builtins.bytearray": ts.BYTEARRAY,
        "tpy.BytesView":      ts.BYTESVIEW,
        "tpy.basic_slice":    ts.BASIC_SLICE,
        "builtins.slice":     ts.SLICE,
        "builtins.list":  ts.make_list(I32),
        "builtins.dict":  ts.make_dict(STR, I32),
        "builtins.set":   ts.make_set(I32),
        "builtins.dict_keys":   ts.make_dict_keys_view(STR, I32),
        "builtins.dict_values": ts.make_dict_values_view(STR, I32),
        "builtins.dict_items":  ts.make_dict_items_view(STR, I32),
        "builtins.Range":  ts.make_range(I32),
        "tpy.Array":    ts.make_array(I32, 10),
        "tpy.Span":     ts.make_span(I32),
        "tpy.varargs":  ts.make_varargs(I32),
        "tpy.SpanIter": ts.make_span_iter(I32),
        "tpy.CopyIter": ts.make_copy_iter(I32),
        "tpy.OwnIter":  ts.make_own_iter(I32),
        "tpy.Ptr":      ts.PtrType(I32),
        "tpy.coro.Waker": ts.WAKER,
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
    # No dedicated subclasses remain for nominal container types; all predicates
    # are keyed on qname.
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
        assert is_range(inst) == (qname == "builtins.Range"), (
            f"is_range({qname}) = {is_range(inst)}"
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


def test_span_element_preserves_readonly_only_for_reference_elements():
    # Span/SpanIter element_of keeps readonly[T] on a reference element (so the
    # readonly-mutation gate sees it) but strips it on a value element (a
    # by-value read drops const). Both views share one helper.
    from tpyc.typesys import ReadonlyType, make_span, make_span_iter, make_list, INT32

    ref_elem = make_list(INT32)  # list[int32] is a reference type
    for view in (make_span(ReadonlyType(ref_elem)), make_span_iter(ReadonlyType(ref_elem))):
        elem = view.get_element_type()
        assert isinstance(elem, ReadonlyType), f"{view}: expected readonly preserved, got {elem}"

    for view in (make_span(ReadonlyType(INT32)), make_span_iter(ReadonlyType(INT32))):
        elem = view.get_element_type()
        assert elem == INT32, f"{view}: expected readonly stripped to int32, got {elem}"


def test_user_spellable_flag_marks_only_internal_builtins():
    # Lock the flag: only the internal *args view is non-spellable today;
    # a regression here would silently re-expose or hide a builtin's name.
    assert get_type_def("tpy.varargs").user_spellable is False
    for qn in ("tpy.Span", "tpy.SpanIter", "tpy.Array", "builtins.list",
               "builtins.dict", "builtins.set"):
        assert get_type_def(qn).user_spellable is True, qn


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


def test_pending_types_do_not_inherit_registry_behavior():
    """Pending* types delegate `qualified_name()` to their resolved builtin,
    so `type_def_of(pending_list)` returns the `list` TypeDef. But base
    `TpyType` methods (subscript_borrows, needs_explicit_element_target,
    is_value_type, is_expensive_copy, param_needs_copy_for_reassign)
    must NOT inherit that registry behavior -- pending types haven't been
    resolved yet and should report class defaults. Regression test for
    the `_nominal_td` guard on the base class methods.
    """
    pending_list = ts.PendingListType(ts.INT32, 0, 0)
    pending_dict = ts.PendingDictType(ts.STR, ts.INT32, 0)
    pending_set = ts.PendingSetType(ts.INT32, 0)

    resolved_list = ts.make_list(ts.INT32)
    # Baseline: the resolved form DOES pick up registry behavior.
    assert resolved_list.subscript_borrows() is True

    # The pending form must NOT pick it up despite sharing the qname.
    for pending in (pending_list, pending_dict, pending_set):
        assert pending.subscript_borrows() is False, (
            f"{type(pending).__name__}.subscript_borrows() leaked "
            f"list/dict/set TypeDef value"
        )
        assert pending.needs_explicit_element_target() is False
        assert pending.is_value_type() is False
        assert pending.is_expensive_copy() is False
        assert pending.param_needs_copy_for_reassign() is False


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
    "tpy.int8":  dict(category=TypeCategory.FIXED_INT, is_value_type=True,
                      is_send=True, is_sync=True, subscript_borrows=False,
                      is_expensive_copy=False, param_needs_copy_for_reassign=False,
                      is_compile_time_only=False,
                      to_cpp="int8_t", to_cpp_param_type="int8_t",
                      element_qname=None, int_bits=8,  int_signed=True),
    "tpy.int16": dict(category=TypeCategory.FIXED_INT, is_value_type=True,
                      is_send=True, is_sync=True, subscript_borrows=False,
                      is_expensive_copy=False, param_needs_copy_for_reassign=False,
                      is_compile_time_only=False,
                      to_cpp="int16_t", to_cpp_param_type="int16_t",
                      element_qname=None, int_bits=16, int_signed=True),
    "tpy.int32": dict(category=TypeCategory.FIXED_INT, is_value_type=True,
                      is_send=True, is_sync=True, subscript_borrows=False,
                      is_expensive_copy=False, param_needs_copy_for_reassign=False,
                      is_compile_time_only=False,
                      to_cpp="int32_t", to_cpp_param_type="int32_t",
                      element_qname=None, int_bits=32, int_signed=True),
    "tpy.int64": dict(category=TypeCategory.FIXED_INT, is_value_type=True,
                      is_send=True, is_sync=True, subscript_borrows=False,
                      is_expensive_copy=False, param_needs_copy_for_reassign=False,
                      is_compile_time_only=False,
                      to_cpp="int64_t", to_cpp_param_type="int64_t",
                      element_qname=None, int_bits=64, int_signed=True),
    "tpy.uint8":  dict(category=TypeCategory.FIXED_INT, is_value_type=True,
                       is_send=True, is_sync=True, subscript_borrows=False,
                       is_expensive_copy=False, param_needs_copy_for_reassign=False,
                       is_compile_time_only=False,
                       to_cpp="uint8_t", to_cpp_param_type="uint8_t",
                       element_qname=None, int_bits=8,  int_signed=False),
    "tpy.uint16": dict(category=TypeCategory.FIXED_INT, is_value_type=True,
                       is_send=True, is_sync=True, subscript_borrows=False,
                       is_expensive_copy=False, param_needs_copy_for_reassign=False,
                       is_compile_time_only=False,
                       to_cpp="uint16_t", to_cpp_param_type="uint16_t",
                       element_qname=None, int_bits=16, int_signed=False),
    "tpy.uint32": dict(category=TypeCategory.FIXED_INT, is_value_type=True,
                       is_send=True, is_sync=True, subscript_borrows=False,
                       is_expensive_copy=False, param_needs_copy_for_reassign=False,
                       is_compile_time_only=False,
                       to_cpp="uint32_t", to_cpp_param_type="uint32_t",
                       element_qname=None, int_bits=32, int_signed=False),
    "tpy.uint64": dict(category=TypeCategory.FIXED_INT, is_value_type=True,
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
    "tpy.float32":    dict(category=TypeCategory.FLOAT, is_value_type=True,
                           is_send=True, is_sync=True, subscript_borrows=False,
                           is_expensive_copy=False, param_needs_copy_for_reassign=False,
                           is_compile_time_only=False,
                           to_cpp="float", to_cpp_param_type="float",
                           element_qname=None, float_bits=32),

    # --- Bool / char -----------------------------------------------------
    "builtins.bool": dict(category=TypeCategory.BOOL, is_value_type=True,
                          is_send=True, is_sync=True, subscript_borrows=False,
                          is_expensive_copy=False, param_needs_copy_for_reassign=False,
                          is_compile_time_only=False,
                          to_cpp="bool", to_cpp_param_type="bool",
                          element_qname=None),
    "tpy.char":      dict(category=TypeCategory.CHAR, is_value_type=True,
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
                         element_qname="tpy.char"),
    "tpy.String":   dict(category=TypeCategory.STR, is_value_type=True,
                         is_send=True, is_sync=True, subscript_borrows=False,
                         is_expensive_copy=True, param_needs_copy_for_reassign=True,
                         is_compile_time_only=False,
                         to_cpp="::tpy::String",
                         to_cpp_param_type="const ::tpy::String&",
                         element_qname="tpy.char"),
    "tpy.StrView":  dict(category=TypeCategory.STR, is_value_type=True,
                         is_send=False, is_sync=True, subscript_borrows=False,
                         is_expensive_copy=False, param_needs_copy_for_reassign=False,
                         is_compile_time_only=False,
                         to_cpp="std::string_view",
                         to_cpp_param_type="std::string_view",
                         element_qname="tpy.char"),
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
                               to_cpp="::tpy::Bytes",
                               to_cpp_param_type="::tpy::BytesView",
                               element_qname="tpy.uint8"),
    "builtins.bytearray": dict(category=TypeCategory.BYTES, is_value_type=False,
                               is_send=True, is_sync=False, subscript_borrows=False,
                               is_expensive_copy=True, param_needs_copy_for_reassign=True,
                               is_compile_time_only=False,
                               to_cpp="::tpy::ByteArray",
                               to_cpp_param_type="const ::tpy::ByteArray&",
                               element_qname="tpy.uint8"),
    "tpy.BytesView":      dict(category=TypeCategory.BYTES, is_value_type=True,
                               is_send=False, is_sync=True, subscript_borrows=False,
                               is_expensive_copy=False, param_needs_copy_for_reassign=False,
                               is_compile_time_only=False,
                               to_cpp="::tpy::BytesView",
                               to_cpp_param_type="::tpy::BytesView",
                               element_qname="tpy.uint8"),

    # --- Slices ----------------------------------------------------------
    "tpy.basic_slice":      dict(category=TypeCategory.SLICE, is_value_type=True,
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
    "tpy.int8":  ts.INT8,  "tpy.int16":  ts.INT16,
    "tpy.int32": ts.INT32, "tpy.int64":  ts.INT64,
    "tpy.uint8": ts.UINT8, "tpy.uint16": ts.UINT16,
    "tpy.uint32": ts.UINT32, "tpy.uint64": ts.UINT64,
    "builtins.int":   ts.BIGINT,
    "builtins.float": ts.FLOAT,
    "tpy.float32":    ts.FLOAT32,
    "builtins.bool":  ts.BOOL,
    "tpy.char":       ts.CHAR,
    "builtins.str":   ts.STR,
    "tpy.String":     ts.STRING,
    "tpy.StrView":    ts.STRVIEW,
    "tpy.FStr":       ts.FSTR,
    "builtins.bytes":     ts.BYTES,
    "builtins.bytearray": ts.BYTEARRAY,
    "tpy.BytesView":      ts.BYTESVIEW,
    "tpy.basic_slice":    ts.BASIC_SLICE,
    "builtins.slice":     ts.SLICE,
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
        is_char_type:        "tpy.char",
        is_float64_type:     "builtins.float",
        is_float32_type:     "tpy.float32",
        is_big_int_type:     "builtins.int",
    }
    # Fixed-int qnames span int8..uint64; is_fixed_int_type is a category
    # predicate, so check it membership-style instead of qname-equality.
    fixed_int_qnames = {
        "tpy.int8", "tpy.int16", "tpy.int32", "tpy.int64",
        "tpy.uint8", "tpy.uint16", "tpy.uint32", "tpy.uint64",
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


# =========================================================================
# Enum hard-coded snapshot (Phase E).
#
# EnumType and IntEnumType are the last nominal-type subclasses. Before
# collapsing them into NominalType + TypeDef.enum, pin their intrinsic
# behavior so the migration path has an independent reference: this
# snapshot is hand-written expected values, not derived from the subclass
# methods, and must keep passing through every migration step (add
# TypeDef.enum -> populate on registration -> flip readers -> delete
# subclass). Mirrors PRIMITIVE_SNAPSHOT's role for Phase D.
# =========================================================================


def _build_enum_snapshot_instances() -> dict[str, "ts.NominalType"]:
    """Build sample enum instances for the snapshot.

    Mirrors what sema/registration.py does: each enum is a NominalType with
    `_module_qname` set (so `type_def_of(t)` resolves), and its EnumInfo
    payload is attached to the TypeDef registry so `enum_info_of(t)`
    returns the members / underlying_type / is_int_enum data. Covers:
    qualified + unqualified (mimicking `__main__`) plain enums, and
    IntEnum variants with default int32 and non-default uint8 underlyings.
    """
    from tpyc.type_def_registry import (
        attach_dynamic_type_def, TypeCategory, EnumInfo as _EnumInfo,
    )
    fixtures = [
        # (short name, module_name for qname, module_name on EnumInfo, is_int_enum, members, underlying)
        ("Color", "palette", "palette", False,
         (("Red", 0), ("Green", 1), ("Blue", 2)), ts.INT32),
        # module_name=None on the EnumInfo (matches __main__ enum), but the
        # qname uses "__main__" as prefix so TypeDef lookup still works.
        ("Status", "__main__", None, False,
         (("Ok", 0), ("Err", 1)), ts.INT32),
        ("Level", "log", "log", True,
         (("Low", 0), ("High", 1)), ts.INT32),
        ("Flags", "perms", "perms", True,
         (("Read", 1), ("Write", 2), ("Exec", 4)), ts.UINT8),
    ]
    result: dict[str, ts.NominalType] = {}
    for name, qname_module, info_module, is_int_enum, member_values, underlying in fixtures:
        members = tuple(m for m, _ in member_values)
        qname = f"{qname_module}.{name}"
        t = ts.NominalType(name=name, type_args=(), _module_qname=qname)
        attach_dynamic_type_def(
            qname, TypeCategory.ENUM,
            enum=_EnumInfo(
                members=members,
                member_values=member_values,
                underlying_type=underlying,
                is_int_enum=is_int_enum,
                module_name=info_module,
            ),
            is_value_type=True,
        )
        result[name] = t
    return result


# Hand-written expected values -- do NOT derive from the instance.
# The point of the snapshot is that these answers survive the migration
# from subclass fields to TypeDef.enum.
ENUM_SNAPSHOT: dict[str, dict] = {
    "Color": dict(
        is_int_enum=False,
        members=("Red", "Green", "Blue"),
        member_values=(("Red", 0), ("Green", 1), ("Blue", 2)),
        underlying_qname="tpy.int32",
        qualified_name="palette.Color",
        info_module_name="palette",
        to_cpp="Color",
        is_value_type=True, is_send=True, is_sync=True,
        subscript_borrows=False, is_expensive_copy=False,
    ),
    "Status": dict(
        is_int_enum=False,
        members=("Ok", "Err"),
        member_values=(("Ok", 0), ("Err", 1)),
        underlying_qname="tpy.int32",
        qualified_name="__main__.Status",
        info_module_name=None,  # __main__ enums preserve None on EnumInfo.module_name
        to_cpp="Status",
        is_value_type=True, is_send=True, is_sync=True,
        subscript_borrows=False, is_expensive_copy=False,
    ),
    "Level": dict(
        is_int_enum=True,
        members=("Low", "High"),
        member_values=(("Low", 0), ("High", 1)),
        underlying_qname="tpy.int32",
        qualified_name="log.Level",
        info_module_name="log",
        to_cpp="Level",
        is_value_type=True, is_send=True, is_sync=True,
        subscript_borrows=False, is_expensive_copy=False,
    ),
    "Flags": dict(
        is_int_enum=True,
        members=("Read", "Write", "Exec"),
        member_values=(("Read", 1), ("Write", 2), ("Exec", 4)),
        underlying_qname="tpy.uint8",
        qualified_name="perms.Flags",
        info_module_name="perms",
        to_cpp="Flags",
        is_value_type=True, is_send=True, is_sync=True,
        subscript_borrows=False, is_expensive_copy=False,
    ),
}


@pytest.mark.parametrize("name", sorted(ENUM_SNAPSHOT))
def test_enum_instance_matches_snapshot(name):
    """Pin enum behavior against hand-coded expected answers. Post-Phase-E
    enums are NominalType + TypeDef.enum; accessor data is read via
    `enum_info_of(t)`."""
    from tpyc.type_def_registry import (
        enum_info_of, is_enum_type, is_int_enum_type,
    )
    expected = ENUM_SNAPSHOT[name]
    instances = _build_enum_snapshot_instances()
    inst = instances[name]
    info = enum_info_of(inst)
    assert info is not None, f"enum_info_of({name}) returned None"

    # Structural identity.
    assert inst.name == name
    assert info.members == expected["members"]
    assert info.member_values == expected["member_values"]
    assert info.underlying_type.qualified_name() == expected["underlying_qname"]
    assert inst.qualified_name() == expected["qualified_name"]
    assert info.module_name == expected["info_module_name"]

    # IntEnum is distinguished by the is_int_enum flag on EnumInfo.
    assert info.is_int_enum == expected["is_int_enum"]
    assert is_int_enum_type(inst) == expected["is_int_enum"]
    assert is_enum_type(inst)

    # Behavior (inherited from NominalType, resolved via TypeDef).
    assert inst.is_value_type() == expected["is_value_type"]
    assert inst.is_send() == expected["is_send"]
    assert inst.is_sync() == expected["is_sync"]
    assert inst.subscript_borrows() == expected["subscript_borrows"]
    assert inst.is_expensive_copy() == expected["is_expensive_copy"]
    assert inst.to_cpp() == expected["to_cpp"]

    # member_value_map should round-trip from member_values.
    assert info.member_value_map == dict(expected["member_values"])


def test_enum_predicates_reject_non_enum_types():
    """enum_info_of / is_enum_type / is_int_enum_type must return None / False
    (not crash) on non-enum inputs. Covers the common cases: primitives,
    containers, structural wrappers, and the tricky LiteralType / PendingView
    siblings whose qualified_name delegates to a base type's qname."""
    from tpyc.type_def_registry import enum_info_of, is_enum_type, is_int_enum_type
    non_enum_cases = [
        ts.INT32, ts.BIGINT, ts.BOOL, ts.STR, ts.FLOAT,
        ts.make_list(ts.INT32),
        ts.make_dict(ts.STR, ts.INT32),
        ts.make_range(ts.INT32),
        ts.OptionalType(ts.INT32),
        ts.TupleType((ts.INT32, ts.STR)),
    ]
    for t in non_enum_cases:
        assert enum_info_of(t) is None, f"enum_info_of({t}) should be None"
        assert not is_enum_type(t), f"is_enum_type({t}) should be False"
        assert not is_int_enum_type(t), f"is_int_enum_type({t}) should be False"


def test_fixed_int_range_bounds_match_width():
    """min_value / max_value are derived from (bits, signed). Pin the
    formula against a few representative widths so Phase D can't silently
    change the range computation."""
    cases = [
        ("tpy.int8",   -2**7,  2**7 - 1),
        ("tpy.int16",  -2**15, 2**15 - 1),
        ("tpy.int32",  -2**31, 2**31 - 1),
        ("tpy.int64",  -2**63, 2**63 - 1),
        ("tpy.uint8",  0, 2**8 - 1),
        ("tpy.uint16", 0, 2**16 - 1),
        ("tpy.uint32", 0, 2**32 - 1),
        ("tpy.uint64", 0, 2**64 - 1),
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
    name -- otherwise resolve_overload could pick a set[int32] overload for a
    list[int32] argument via the recursive element-matching branch."""
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


# =========================================================================
# Dynamic TypeDef attachment (Phase E step 2).
#
# sema/registration.py attaches RecordInfo / ProtocolInfo onto the TypeDef
# registry as records and protocols come through register_record /
# register_protocol. The tests here pin the attach-and-clear behavior in
# isolation, without going through a full compile. The conftest autouse
# fixture calls clear_all_compilation_state() around every test, so each
# case starts from a clean dynamic slate.
# =========================================================================


def test_attach_dynamic_creates_new_typedef():
    """Purely-dynamic qname (no static entry): attach creates a TypeDef."""
    from tpyc.type_def_registry import attach_dynamic_type_def, clear_dynamic_type_defs
    qname = "test_module.TestProtocol"
    assert get_type_def(qname) is None
    stub = object()  # opaque ProtocolInfo stand-in
    td = attach_dynamic_type_def(qname, TypeCategory.PROTOCOL, protocol=stub)
    assert td is get_type_def(qname)
    assert td.category is TypeCategory.PROTOCOL
    assert td.protocol is stub
    assert td.record is None
    clear_dynamic_type_defs()
    assert get_type_def(qname) is None


def test_record_and_protocol_attach_during_compile():
    """End-to-end: compile a module with a user protocol and verify that the
    TypeDef registry has the ProtocolInfo attached under its qname. The
    conformance tests alone don't hit the sema hook -- this pins that the
    hook actually fires for real compiles. Looks up the TypeDef by qname
    directly so the assertion doesn't depend on NominalType.qualified_name()
    walking the `_protocol_modules` side-channel.

    Note: `Compiler.from_source` compiles the source as the entry point, so
    its runtime module identity is `__main__` regardless of the `module_name`
    argument (which only names the generated file). Protocol registration
    uses the runtime module, so the qname falls into the `__main__.<Name>`
    branch of register_protocol."""
    from tpyc import get_lib_dir
    from tpyc.compiler import Compiler
    source = (
        "from typing import Protocol\n"
        "class Greeter(Protocol):\n"
        "    def greet(self) -> str: ...\n"
    )
    compiler = Compiler.from_source(source,
                                    lib_dirs=[get_lib_dir() / "tpy"])
    compiler.compile()
    # Keep the compilation's dynamic_type_defs slice visible while we
    # inspect the registry post-compile.
    with activate_compiler(compiler):
        td = get_type_def("__main__.Greeter")
    assert td is not None, (
        "TypeDef for '__main__.Greeter' should exist after sema registers the protocol."
    )
    assert td.category is TypeCategory.PROTOCOL
    assert td.protocol is not None, "TypeDef.protocol payload should be attached"
    assert td.protocol.name == "Greeter"
    assert any(m.name == "greet" for m in td.protocol.methods)


def test_attach_dynamic_updates_existing_typedef():
    """Pre-existing static qname (e.g. builtins.list): attach updates only the
    record/protocol payload; category and cpp_formatter are preserved."""
    from tpyc.type_def_registry import attach_dynamic_type_def, clear_dynamic_type_defs
    td_before = get_type_def("builtins.list")
    assert td_before is not None
    assert td_before.category is TypeCategory.LIST
    original_category = td_before.category
    original_cpp_formatter = td_before.cpp_formatter
    original_record = td_before.record

    stub = object()
    attach_dynamic_type_def("builtins.list", TypeCategory.RECORD, record=stub)
    td_after = get_type_def("builtins.list")

    # Category and cpp_formatter survive the attachment -- only payloads change.
    assert td_after.category is original_category
    assert td_after.cpp_formatter is original_cpp_formatter
    assert td_after.record is stub

    clear_dynamic_type_defs()
    td_cleared = get_type_def("builtins.list")
    # Static TypeDef remains; record payload resets to the pre-attach value.
    assert td_cleared is not None
    assert td_cleared.category is original_category
    assert td_cleared.record is original_record


# =========================================================================
# Factory payload snapshot (Phase F.3e).
#
# Hard-coded golden table of `param_kinds` arity + kind for every factory
# entry that used to live in `modules/type_resolution.py`. Pins the
# contract that the merged TypeDef registry must honor. As with
# PRIMITIVE_SNAPSHOT, hand-maintained -- regenerating from the registry
# would defeat the check.
# =========================================================================


def _k(kind_name: str):
    """Shorthand for TypeParamKind enum members (imported lazily to avoid
    adding a top-level typesys import to this file)."""
    from tpyc.typesys import TypeParamKind
    return getattr(TypeParamKind, kind_name)


# qname -> tuple of TypeParamKind strings ("TYPE" / "INT"). Empty tuple
# means "no type args" (primitive singletons).
FACTORY_SNAPSHOT: dict[str, tuple[str, ...]] = {
    # Containers
    "builtins.list":        ("TYPE",),
    "builtins.dict":        ("TYPE", "TYPE"),
    "builtins.dict_keys":   ("TYPE", "TYPE"),
    "builtins.dict_values": ("TYPE", "TYPE"),
    "builtins.dict_items":  ("TYPE", "TYPE"),
    "builtins.set":         ("TYPE",),
    "builtins.Range":       ("TYPE",),
    "tpy.Array":            ("TYPE", "INT"),
    "tpy.Span":             ("TYPE",),
    "tpy.SpanIter":         ("TYPE",),
    "tpy.coro.Waker":       (),
    # Structural wrapper
    "tpy.Ptr":              ("TYPE",),
    # Primitive singletons
    "tpy.float32":    (),
    "tpy.char":       (),
    "tpy.String":     (),
    "tpy.StrView":    (),
    "tpy.FStr":       (),
    "builtins.int":   (),
    "builtins.float": (),
    "builtins.bool":  (),
    "builtins.str":   (),
    "builtins.bytes":     (),
    "builtins.bytearray": (),
    "tpy.BytesView":      (),
    "tpy.basic_slice":    (),
    "builtins.slice":     (),
    "tpy.int8":  (), "tpy.int16": (), "tpy.int32": (), "tpy.int64": (),
    "tpy.uint8": (), "tpy.uint16": (), "tpy.uint32": (), "tpy.uint64": (),
}


def test_factory_snapshot_covers_every_registered_factory():
    """Every TypeDef with a `type_factory` must have a FACTORY_SNAPSHOT
    entry, and vice versa. Protects against silent drift when someone adds
    or removes a factory without updating the golden table."""
    registered = {qn for qn, td in _type_defs.items() if td.type_factory is not None}
    missing = registered - set(FACTORY_SNAPSHOT)
    assert not missing, (
        f"Registered factory qnames without a FACTORY_SNAPSHOT entry: {missing}."
    )
    extra = set(FACTORY_SNAPSHOT) - registered
    assert not extra, (
        f"FACTORY_SNAPSHOT entries that aren't registered factories: {extra}."
    )


@pytest.mark.parametrize("qname", sorted(FACTORY_SNAPSHOT))
def test_factory_param_kinds_match_snapshot(qname):
    """`TypeDef.param_kinds` must match the golden arity/kinds table."""
    td = get_type_def(qname)
    assert td is not None
    expected = tuple(_k(name) for name in FACTORY_SNAPSHOT[qname])
    actual = tuple(td.param_kinds)
    assert actual == expected, (
        f"{qname}: TypeDef.param_kinds={actual!r} but snapshot={expected!r}"
    )


def test_factory_produces_same_instance_as_registry():
    """`TypeDef.type_factory(...)` with canonical args must produce a
    TpyType whose qualified name equals the TypeDef qname. Catches wiring
    mistakes (e.g. list's factory producing a dict) without pinning every
    factory's exact return value."""
    I32 = ts.INT32
    canonical_args: dict[str, tuple] = {
        "builtins.list":        (I32,),
        "builtins.dict":        (ts.STR, I32),
        "builtins.dict_keys":   (ts.STR, I32),
        "builtins.dict_values": (ts.STR, I32),
        "builtins.dict_items":  (ts.STR, I32),
        "builtins.set":         (I32,),
        "builtins.Range":       (I32,),
        "tpy.Array":            (I32, 10),
        "tpy.Span":             (I32,),
        "tpy.SpanIter":         (I32,),
        "tpy.Ptr":              (I32,),
    }
    for qname, args in canonical_args.items():
        td = get_type_def(qname)
        assert td is not None and td.type_factory is not None
        produced = td.type_factory(*args)
        assert produced.qualified_name() == qname, (
            f"{qname}: factory produced {produced!r} with qname "
            f"{produced.qualified_name()!r}"
        )

    # Zero-arg factories must return the typesys singleton with the same qname.
    zero_arg_qnames = [qn for qn, kinds in FACTORY_SNAPSHOT.items() if not kinds]
    for qname in zero_arg_qnames:
        td = get_type_def(qname)
        assert td is not None and td.type_factory is not None
        produced = td.type_factory()
        assert produced.qualified_name() == qname, (
            f"{qname}: zero-arg factory produced {produced!r} with qname "
            f"{produced.qualified_name()!r}"
        )


def test_structural_wrapper_category_only_holds_ptr():
    """The STRUCTURAL_WRAPPER category exists only for `tpy.Ptr`. If a new
    entry gets added, the author must update the resolver / sema paths
    that currently key on Ptr being the sole structural-wrapper factory."""
    wrappers = {qn for qn, td in _type_defs.items()
                if td.category is TypeCategory.STRUCTURAL_WRAPPER}
    assert wrappers == {"tpy.Ptr"}, (
        f"Unexpected STRUCTURAL_WRAPPER entries: {wrappers}"
    )


def test_find_factory_helpers_match_old_lookup():
    """`find_factory_by_simple_name` and `find_factory_in_module` must
    resolve to the same qname as the old `lookup_generic_type` /
    `lookup_generic_type_in_module` factory table."""
    from tpyc.type_def_registry import (
        find_factory_by_simple_name, find_factory_in_module,
        factory_qnames_in_module,
    )
    # Simple-name lookup scans builtins then tpy.
    assert find_factory_by_simple_name("list").qname == "builtins.list"
    assert find_factory_by_simple_name("Span").qname == "tpy.Span"
    assert find_factory_by_simple_name("Ptr").qname == "tpy.Ptr"
    assert find_factory_by_simple_name("definitely_unknown") is None

    # Module-qualified lookup.
    assert find_factory_in_module("Array", "tpy").qname == "tpy.Array"
    assert find_factory_in_module("list", "builtins").qname == "builtins.list"
    # Wrong module -> None.
    assert find_factory_in_module("Array", "builtins") is None
    assert find_factory_in_module("list", "tpy") is None

    # Enumeration helper: every tpy.* factory qname must appear.
    tpy_factories = set(factory_qnames_in_module("tpy"))
    expected_tpy = {qn for qn in FACTORY_SNAPSHOT if qn.startswith("tpy.")}
    assert tpy_factories == expected_tpy


def test_fixed_int_names_stay_in_sync():
    """Four copies of the fixed-int name list exist: parser's hardcoded
    set, the literal_utils and macro_api sets derived from ALL_FIXED_INTS,
    and qnames.FIXED_INT_NAMES (hardcoded; qnames cannot import typesys).
    If a new fixed-int width ever lands in typesys.ALL_FIXED_INTS, the
    hardcoded copies must update in lockstep; this test pins the
    invariant so a single addition surfaces all sites at once.

    codegen_cpp.functions._SCALAR_ZERO_CTOR_NAMES is intentionally
    broader (adds `int`/`float`/`bool` for zero-arg scalar ctor codegen)
    and is not covered here.
    """
    expected = frozenset(str(t) for t in ALL_FIXED_INTS)
    assert PARSER_FIXED_INT_NAMES == expected, (
        f"parser._FIXED_INT_NAMES drifted from ALL_FIXED_INTS: "
        f"missing={expected - PARSER_FIXED_INT_NAMES}, "
        f"extra={PARSER_FIXED_INT_NAMES - expected}"
    )
    assert LITERAL_UTILS_FIXED_INT_NAMES == expected
    assert MACRO_FIXED_INT_NAMES == expected
    assert frozenset(qnames.FIXED_INT_NAMES.values()) == expected, (
        "qnames.FIXED_INT_NAMES drifted from ALL_FIXED_INTS")


# =========================================================================
# Protocol snapshot (TypeRegistry.protocols retirement).
#
# Hand-written golden table of ProtocolInfo fields for every stdlib
# protocol the compiler registers when `typing` + `tpy` are imported.
# The retirement of the short-name `TypeRegistry.protocols` dict in
# favor of qname-keyed `TypeDef.protocol` is an invariance refactor:
# every qname below must still resolve to the same ProtocolInfo
# payload after each migration step (flipping callers to
# `protocol_info_of`, reshaping storage, deleting the legacy dict).
#
# Mirrors PRIMITIVE_SNAPSHOT (primitive migration) and ENUM_SNAPSHOT
# (enum migration). Hand-maintained -- regenerating from the registry
# would defeat the check.
# =========================================================================


PROTOCOL_SNAPSHOT: dict[str, dict] = {
    # typing.* protocols (tpy._typing, collapsed via cpp_namespace="tpystd::typing")
    "typing.Sized": dict(
        name="Sized", module="typing",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=(), parent_protocols=(),
        methods=("__len__",),
    ),
    "typing.Sequence": dict(
        name="Sequence", module="typing",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=("T",), parent_protocols=(),
        methods=("__getitem__", "__len__"),
    ),
    "typing.MutableSequence": dict(
        name="MutableSequence", module="typing",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=("T",), parent_protocols=(),
        methods=("__getitem__", "__len__", "__setitem__"),
    ),
    "typing.Iterator": dict(
        name="Iterator", module="typing",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=("T",), parent_protocols=(),
        methods=("__iter__", "__next__"),
    ),
    "typing.Iterable": dict(
        name="Iterable", module="typing",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=("T",), parent_protocols=(),
        methods=("__iter__",),
    ),
    # tpy.* @dynamic protocols (tpy._core._types)
    "tpy.Throwable": dict(
        name="Throwable", module="tpy",
        is_dynamic=True, is_marker=False, is_readonly=False,
        cpp_concept="::tpy::Throwable",
        type_params=(), parent_protocols=(),
        methods=("__raise__", "clone"),
    ),
    # tpy.* structural protocols (tpy._core._types)
    "tpy.coro.Awaitable": dict(
        name="Awaitable", module="tpy.coro",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=("T",), parent_protocols=(),
        methods=("__poll__",),
    ),
    "tpy.coro.Awaker": dict(
        name="Awaker", module="tpy.coro",
        is_dynamic=True, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=(), parent_protocols=(),
        methods=("mark_runnable",),
    ),
    "tpy.coro.Cancellable": dict(
        name="Cancellable", module="tpy.coro",
        is_dynamic=True, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=("T",), parent_protocols=(),
        methods=("__poll__", "cancel"),
    ),
    "tpy.Truthy": dict(
        name="Truthy", module="tpy",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=(), parent_protocols=(),
        methods=("__bool__",),
    ),
    "tpy.Stringable": dict(
        name="Stringable", module="tpy",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=(), parent_protocols=(),
        methods=("__str__",),
    ),
    "tpy.Representable": dict(
        name="Representable", module="tpy",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=(), parent_protocols=(),
        methods=("__repr__",),
    ),
    "tpy.Hashable": dict(
        name="Hashable", module="tpy",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=(), parent_protocols=(),
        methods=("__hash__",),
    ),
    "tpy.Comparable": dict(
        name="Comparable", module="tpy",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=(), parent_protocols=(),
        methods=("__lt__",),
    ),
    "tpy.Equatable": dict(
        name="Equatable", module="tpy",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=(), parent_protocols=(),
        methods=("__eq__",),
    ),
    "tpy.Deref": dict(
        name="Deref", module="tpy",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=("T",), parent_protocols=(),
        methods=("__deref__",),
    ),
    "tpy.Spannable": dict(
        name="Spannable", module="tpy",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=("T",), parent_protocols=(),
        methods=("__span__",),
    ),
    "tpy.Writable": dict(
        name="Writable", module="tpy",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=(), parent_protocols=(),
        methods=("flush", "write"),
    ),
    "tpy.Readable": dict(
        name="Readable", module="tpy",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=(), parent_protocols=(),
        methods=("read", "readline"),
    ),
    "tpy.BinaryWritable": dict(
        name="BinaryWritable", module="tpy",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=(), parent_protocols=(),
        methods=("flush", "write"),
    ),
    "tpy.BinaryReadable": dict(
        name="BinaryReadable", module="tpy",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=(), parent_protocols=(),
        methods=("read", "readline"),
    ),
    "tpy.Seekable": dict(
        name="Seekable", module="tpy",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=(), parent_protocols=(),
        methods=("seek", "tell"),
    ),
    "tpy.Closable": dict(
        name="Closable", module="tpy",
        is_dynamic=False, is_marker=False, is_readonly=False,
        cpp_concept=None,
        type_params=(), parent_protocols=(),
        methods=("close",),
    ),
    # tpy.* @native marker / concept-backed protocols
    "tpy.NativeIterable": dict(
        name="NativeIterable", module="tpy",
        is_dynamic=False, is_marker=True, is_readonly=False,
        cpp_concept="::tpy::NativeIterable",
        type_params=("T",),
        parent_protocols=(
            ts.NominalType(
                name="Iterable",
                type_args=(ts.TypeParamRef(
                    name="T", bound=None, kind=ts.TypeParamKind.TYPE,
                ),),
                is_protocol=True,
                _module_qname="typing.Iterable",
            ),
        ),
        methods=(),
    ),
    "tpy.NativeRangeConstructible": dict(
        name="NativeRangeConstructible", module="tpy",
        is_dynamic=False, is_marker=True, is_readonly=False,
        cpp_concept="::tpy::NativeRangeConstructible",
        type_params=("T",), parent_protocols=(),
        methods=(),
    ),
    "tpy.ValueType": dict(
        name="ValueType", module="tpy",
        is_dynamic=False, is_marker=True, is_readonly=False,
        cpp_concept="::tpy::ValueType",
        type_params=(), parent_protocols=(),
        methods=(),
    ),
    "tpy.Copyable": dict(
        name="Copyable", module="tpy",
        is_dynamic=False, is_marker=True, is_readonly=False,
        cpp_concept="::tpy::Copyable",
        type_params=(), parent_protocols=(),
        methods=(),
    ),
    "tpy.Send": dict(
        name="Send", module="tpy",
        is_dynamic=False, is_marker=True, is_readonly=False,
        cpp_concept="::tpy::Send",
        type_params=(), parent_protocols=(),
        methods=(),
    ),
    "tpy.Sync": dict(
        name="Sync", module="tpy",
        is_dynamic=False, is_marker=True, is_readonly=False,
        cpp_concept="::tpy::Sync",
        type_params=(), parent_protocols=(),
        methods=(),
    ),
    "tpy.Default": dict(
        name="Default", module="tpy",
        is_dynamic=False, is_marker=True, is_readonly=False,
        cpp_concept="::std::default_initializable",
        type_params=(), parent_protocols=(),
        methods=(),
    ),
    "tpy.ReturnException": dict(
        name="ReturnException", module="tpy",
        is_dynamic=False, is_marker=True, is_readonly=False,
        cpp_concept="::tpy::ReturnException",
        type_params=(), parent_protocols=(),
        methods=(),
    ),
    "tpy.Covariant": dict(
        name="Covariant", module="tpy",
        is_dynamic=False, is_marker=True, is_readonly=False,
        cpp_concept="::tpy::Covariant",
        type_params=("T",), parent_protocols=(),
        methods=(),
    ),
    "tpy.AnyFixedInt": dict(
        name="AnyFixedInt", module="tpy",
        is_dynamic=False, is_marker=True, is_readonly=False,
        cpp_concept="::tpy::AnyFixedInt",
        type_params=(), parent_protocols=(),
        methods=(),
    ),
    "tpy.AnyFixedSigned": dict(
        name="AnyFixedSigned", module="tpy",
        is_dynamic=False, is_marker=True, is_readonly=False,
        cpp_concept="::tpy::AnyFixedSigned",
        type_params=(), parent_protocols=(),
        methods=(),
    ),
    "tpy.AnyFixedUnsigned": dict(
        name="AnyFixedUnsigned", module="tpy",
        is_dynamic=False, is_marker=True, is_readonly=False,
        cpp_concept="::tpy::AnyFixedUnsigned",
        type_params=(), parent_protocols=(),
        methods=(),
    ),
}


_PROTOCOL_SNAPSHOT_SOURCE = """\
from typing import Sized, Iterable, Iterator, Sequence, MutableSequence
from tpy import (
    Truthy, Stringable, Representable, Hashable, Comparable, Equatable,
    Deref, Spannable, Writable, Readable, BinaryWritable, BinaryReadable,
    Seekable, Closable,
    NativeIterable, NativeRangeConstructible, ValueType, Send, Sync,
    Default, ReturnException, Covariant,
    AnyFixedInt, AnyFixedSigned, AnyFixedUnsigned,
)
from tpy.coro import Awaitable, Cancellable
"""


@pytest.fixture(scope="module")
def _protocol_snapshot_compiled():
    """Compile a source that imports every protocol in PROTOCOL_SNAPSHOT.

    Scoped to the module so the compile runs once; the conftest autouse
    fixture clears dynamic state around each test, so individual tests
    that need the populated registry recompile via this fixture.
    """
    from tpyc import get_lib_dir
    from tpyc.compiler import Compiler
    compiler = Compiler.from_source(
        _PROTOCOL_SNAPSHOT_SOURCE, lib_dirs=[get_lib_dir() / "tpy"]
    )
    compiler.compile()
    return compiler


@pytest.fixture
def _protocol_registry(_protocol_snapshot_compiled):
    """Per-test fixture: recompile the snapshot source so the TypeDef
    registry is populated inside the test's `clear_all_compilation_state`
    window. The module-scoped fixture above is retained for any future
    callers that legitimately want the pre-clear state.

    The fixture keeps the recompiled Compiler active for the test body
    so reads of `compiler.dynamic_type_defs` (via `get_type_def` etc.)
    see this compilation's slice, not the conftest fake context's empty
    one.
    """
    from tpyc import get_lib_dir
    from tpyc.compiler import Compiler
    compiler = Compiler.from_source(
        _PROTOCOL_SNAPSHOT_SOURCE, lib_dirs=[get_lib_dir() / "tpy"]
    )
    compiler.compile()
    with activate_compiler(compiler):
        yield compiler


def test_protocol_snapshot_covers_every_compiled_protocol(_protocol_registry):
    """Every PROTOCOL-category TypeDef the snapshot compile populates must
    have a PROTOCOL_SNAPSHOT entry (and vice versa). Guards against silent
    drift: a new stdlib protocol without a snapshot entry, or a snapshot
    entry pointing at a qname the registration path no longer creates."""
    # PROTOCOL TypeDefs live in two places: pre-existing static entries
    # whose payload was attached during compile (mostly @builtin_type
    # stub protocols), and purely-dynamic entries on the active
    # compiler's `dynamic_type_defs`. Both contribute.
    live = {
        qn for qn, td in _type_defs.items()
        if td.category is TypeCategory.PROTOCOL and td.protocol is not None
    }
    live |= {
        qn for qn, td in _protocol_registry.dynamic_type_defs.items()
        if td.category is TypeCategory.PROTOCOL and td.protocol is not None
    }
    missing = live - set(PROTOCOL_SNAPSHOT)
    assert not missing, (
        f"Protocol qnames registered by the snapshot compile without a "
        f"PROTOCOL_SNAPSHOT entry: {sorted(missing)}. Add them with "
        f"their expected ProtocolInfo fields."
    )
    extra = set(PROTOCOL_SNAPSHOT) - live
    assert not extra, (
        f"PROTOCOL_SNAPSHOT entries that the compile didn't register: "
        f"{sorted(extra)}. Either the registration path regressed or the "
        f"snapshot names a qname that no longer exists."
    )


@pytest.mark.parametrize("qname", sorted(PROTOCOL_SNAPSHOT))
def test_protocol_type_def_matches_snapshot(qname, _protocol_registry):
    """Hand-coded expected values for every stdlib protocol survive the
    retirement of TypeRegistry.protocols. Reading goes through
    `TypeDef.protocol` (qname-keyed), which is authoritative; after each
    migration step, every field below must still match."""
    td = get_type_def(qname)
    assert td is not None, f"No TypeDef for {qname}"
    assert td.category is TypeCategory.PROTOCOL, (
        f"{qname}: TypeDef.category={td.category}, expected PROTOCOL"
    )
    info = td.protocol
    assert info is not None, f"{qname}: TypeDef.protocol payload missing"
    expected = PROTOCOL_SNAPSHOT[qname]

    assert info.name == expected["name"]
    assert info.module == expected["module"]
    assert info.is_dynamic == expected["is_dynamic"]
    assert info.is_marker == expected["is_marker"]
    assert info.is_readonly == expected["is_readonly"]
    assert info.cpp_concept == expected["cpp_concept"]
    assert tuple(info.type_params) == expected["type_params"]
    assert tuple(info.parent_protocols) == expected["parent_protocols"]
    assert tuple(sorted(m.name for m in info.methods)) == expected["methods"]


@pytest.mark.parametrize("qname", sorted(PROTOCOL_SNAPSHOT))
def test_protocol_info_of_matches_type_def(qname, _protocol_registry):
    """`protocol_info_of(NominalType(qname))` must return the same
    ProtocolInfo instance as the direct `TypeDef.protocol` lookup. This
    pins the migration target: all callers eventually route through
    `protocol_info_of` and must see identical data."""
    from tpyc.type_def_registry import protocol_info_of
    td = get_type_def(qname)
    assert td is not None and td.protocol is not None
    # Synthesize a NominalType carrying the qname; protocol_info_of goes
    # through `type_def_of(t)` which keys on `qualified_name()`.
    module, _, short = qname.rpartition(".")
    inst = ts.NominalType(
        name=short, type_args=(), is_protocol=True, _module_qname=qname,
    )
    info = protocol_info_of(inst)
    assert info is td.protocol, (
        f"{qname}: protocol_info_of returned {info!r}, TypeDef.protocol={td.protocol!r}"
    )


def test_scan_by_short_name_honors_import_alias():
    """`register_protocol(info, local_name)` must make `scan_by_short_name`
    resolve both the protocol's canonical short name and any local import
    alias to the same `ProtocolInfo`.

    The alias path is the load-bearing reason the short-name
    `_protocols_by_local_name` table exists at all; without it the
    short-name helper could be replaced by a scan over qnames. Pin the
    semantics here so the helper's contract doesn't silently regress when
    the storage layer evolves further (e.g. a future move to TypeDef
    discovery).

    Uses a synthetic ProtocolInfo rather than a real compile so the test
    stays fast and doesn't depend on the multi-module import plumbing.
    """
    registry = ts.TypeRegistry()
    info = ts.ProtocolInfo(
        name="Sized",
        methods=[],
        module="typing",
    )
    registry.register_protocol(info, "MySized")

    # Alias resolves to the original ProtocolInfo.
    assert registry.scan_by_short_name("MySized") is info

    # Canonical name is NOT auto-aliased when a local name is provided;
    # only the explicit binding is recorded.
    assert registry.scan_by_short_name("Sized") is None

    # A second registration without a local-name override exposes the
    # canonical short name too -- mimicking the typical stdlib-import
    # path in `compiler.py::_analyze_module`.
    registry.register_protocol(info)
    assert registry.scan_by_short_name("Sized") is info
    assert registry.scan_by_short_name("MySized") is info  # alias still works

    # Unknown name returns None, not a random collision.
    assert registry.scan_by_short_name("Nonexistent") is None


def test_resolve_type_for_codegen_does_not_promote_record_to_protocol():
    """`ProtocolGenerator.resolve_type_for_codegen` probes `protocol_info_of`
    with `is_protocol=True` set on the NominalType. When `_module_qname`
    points at a RECORD-category TypeDef (short name happens to collide with
    a protocol in another module), the probe must return None and the
    type must stay classified as a record.

    The pre-P.1 path used a short-name `registry.get_protocol(typ.name)`
    lookup that would have matched the collision and silently promoted
    the record. Pinning the qname-driven behavior here prevents a
    regression to the short-name semantics during any future reshape of
    `resolve_type_for_codegen`.
    """
    from tpyc.type_def_registry import (
        attach_dynamic_type_def, TypeCategory,
    )
    # A user record at `user_mod.Widget`.
    record_qname = "user_mod.Widget"
    record_info = ts.RecordInfo(
        name="Widget",
        module="user_mod",
        fields=[],
        methods={},
    )
    attach_dynamic_type_def(record_qname, TypeCategory.RECORD, record=record_info)

    # An unrelated protocol also named `Widget` in a different module --
    # this would have short-name-matched under the old lookup.
    protocol_qname = "other_mod.Widget"
    protocol_info = ts.ProtocolInfo(
        name="Widget", methods=[], module="other_mod",
    )
    attach_dynamic_type_def(protocol_qname, TypeCategory.PROTOCOL,
                            protocol=protocol_info)

    # NominalType bound to the record qname, not flagged as protocol.
    record_nominal = ts.NominalType(
        name="Widget", type_args=(), is_protocol=False,
        _module_qname=record_qname,
    )

    # Build a minimal ProtocolGenerator with just enough context to call
    # resolve_type_for_codegen; the method only reaches into
    # `protocol_info_of`, not into `ctx.analyzer.*`.
    from tpyc.codegen_cpp.protocols import ProtocolGenerator

    class _StubCtx:
        pass

    pg = ProtocolGenerator(_StubCtx())
    resolved = pg.resolve_type_for_codegen(record_nominal)

    # Record must NOT be flipped to a protocol.
    assert resolved is record_nominal, (
        "resolve_type_for_codegen must leave a record-qname NominalType "
        "unchanged even when a protocol of the same short name exists "
        f"elsewhere; got {resolved!r}"
    )
    assert resolved.is_protocol is False

    # Sanity: a placeholder NominalType (no _module_qname) whose short
    # name resolves to the `other_mod` protocol via `_protocol_modules`
    # *should* get flipped -- this is the legitimate promotion case.
    from tpyc.typesys import register_protocol_module
    register_protocol_module("Widget", "other_mod")
    placeholder = ts.NominalType(name="Widget", type_args=(), is_protocol=False)
    promoted = pg.resolve_type_for_codegen(placeholder)
    assert promoted.is_protocol is True
    assert promoted.qualified_name() == protocol_qname


def test_view_param_form_set_is_str_and_bytes():
    """`has_view_param_form` -- a type whose PARAM form is a distinct view
    over its own storage form -- must derive to exactly `str` and `bytes`.

    KEY: every entry of `type_def_registry._type_defs` that registers BOTH a
    `cpp_formatter` and a `param_cpp_formatter`, asked through the public
    predicate on a bare NominalType of that qname. The generic TypeDefs
    (list, dict, Span, ...) register no param formatter and default to
    `<storage>&`, which the predicate excludes by construction.

    The set is pinned rather than left to a corpus sweep: `String` and
    `bytearray` pass a REFERENCE to their storage and must stay out, or the
    runtime row below would give them a view slot they cannot bind.
    """
    from tpyc.type_def_registry import _type_defs, has_view_param_form

    scanned = set()
    view_param = set()
    for qname, td in _type_defs.items():
        if td.cpp_formatter is None or td.param_cpp_formatter is None:
            continue
        scanned.add(qname)
        t = ts.NominalType(name=qname.rsplit(".", 1)[-1], type_args=(),
                           _module_qname=qname)
        if has_view_param_form(t):
            view_param.add(qname)

    # Floor on the scan itself: a lookup that silently collapsed would make
    # the assertion below pass for the wrong reason.
    assert len(scanned) >= 15, f"formatter scan collapsed: {sorted(scanned)}"
    assert view_param == {"builtins.str", "builtins.bytes"}, sorted(view_param)
    assert "tpy.String" in scanned and "builtins.bytearray" in scanned


def test_view_param_form_matches_the_runtime_trait_rows():
    """The registry's view-param set and the runtime's
    `param_val_or_ref_impl` rows are the same fact in two languages.

    A generic `T` parameter renders `::tpy::param_val_or_ref_t<T>`, whose
    default is `const T&` for a value type -- the OWNED form. A type whose TPy
    parameter form is a distinct view therefore needs a row in the runtime, or
    a generic slot at it stops matching the monomorphic twin and the caller has
    to materialize a copy (the defect the distinct C++ types removed). The
    reverse drift is worse: a row for a type whose param form is NOT a view
    would hand a generic body a view of a buffer it is supposed to own.

    KEY: registry side, every value TypeDef `has_view_param_form` answers True
    for, rendered through its own `cpp_formatter`; runtime side, every
    `param_val_or_ref_impl<X>` specialization under
    `runtime/cpp/include/tpy/` except the `void` row, which is trait
    machinery (`val_or_ref_t<void>` lets `def f[T] -> T` instantiate at None).
    Both normalized by stripping a leading `::`.
    """
    import re

    from tpyc import get_runtime_dir
    from tpyc.type_def_registry import _type_defs, has_view_param_form

    registry = set()
    for qname, td in _type_defs.items():
        if td.cpp_formatter is None or td.param_cpp_formatter is None:
            continue
        t = ts.NominalType(name=qname.rsplit(".", 1)[-1], type_args=(),
                           _module_qname=qname)
        if has_view_param_form(t):
            registry.add(td.cpp_formatter(()).removeprefix("::"))

    include = get_runtime_dir() / "cpp" / "include" / "tpy"
    text = "\n".join(f.read_text() for f in sorted(include.rglob("*.hpp")))
    pat = re.compile(r"struct\s+param_val_or_ref_impl<([^;>]+)>\s*\{")
    def qualify(head: str) -> str:
        # A row inside `namespace tpy` spells its own types unqualified.
        return head if "::" in head else f"tpy::{head}"

    rows = {qualify(h) for h in
            ({m.group(1).strip().removeprefix("::")
              for m in pat.finditer(text)} - {"void"})}
    assert rows, "the param_val_or_ref_impl scan found no rows at all"
    assert registry == rows, (
        f"registry view-param types {sorted(registry)} != runtime rows "
        f"{sorted(rows)}")
