"""The compiler and the runtime must agree on which types are value types.

`tpyc/type_def_registry.py` decides value-vs-reference for the front end; the
C++ runtime decides it again for every generic body -- `param_val_or_ref_t<T>`,
`val_or_ref_t<T>`, `val_or_ptr_t<T>`, `opt_param_t<T>` and the `ValueType`
concept all read `tpy::is_value_type<T>`. The two lists are written by hand in
different languages, so they drift: before this guard the runtime called `float`
(Float32), `std::span<const uint8_t>` (BytesView), the spans, the dict views,
Range and the slices reference types, which made a generic slot at any of them a
MUTABLE `T&`. `bytes` was undecidable until it stopped sharing
`std::vector<uint8_t>` with `bytearray` and `list[UInt8]`; the one entry left in
UNDECIDABLE is the C++ type the runtime mints for its own borrows.

A runtime type declares its own value-ness next to its definition (span_iter,
slice, range, varargs, dict_ops); `type_traits.hpp` keeps only the rows whose
type has no defining header of ours. The scan therefore reads EVERY header under
`runtime/cpp/include/tpy/`, not one file -- a row that moves to a new home stays
visible, and a row deleted in any of them fails here.

The KEY, so a false green is visible:

- registry side -- every entry in `tpyc.type_def_registry._type_defs` with
  `is_value_type=True`, rendered through its own `cpp_formatter` at canonical
  type args, reduced to the head of the C++ type (the text before `<`). The
  `is_send` / `is_sync` fields of the same entries, resolved at those args,
  drive the second pair of assertions.
- runtime side -- every `struct is_value_type<X> : std::true_type` in those
  headers (and `struct is_send<X> / is_sync<X> : std::false_type` for the second
  pair), reduced the same way. Names are resolved against `namespace tpy` (an
  unqualified head is `tpy::X` unless it is a fundamental or `<cstdint>` type),
  so the comparison is over qualified names, not bare identifiers.

`is_send` and `is_sync` default to `is_value_type`, so every value type the
header gains has to spell its own False if the registry says it is not Send or
not Sync -- a borrowing view (`varargs`, the dict views) would otherwise become
Send the moment it becomes a value type. That direction is the only one checked:
a True verdict needs no row.

Both sides are re-derived on every run, and MIN_* floors below fail if either
scan collapses to a handful of rows.

Out of scope, by construction:

- USER value types (`class P(ValueType)`) render a user C++ type that no
  hand-written header can know; codegen emits their specialization next to the
  struct (`generator.py:_emit_value_type_spec`). Same for the `@builtin_type`
  value records in lib/tpy (e.g. `tpy.coro.Waker`).
- ENUMS are per-compilation TypeDefs, so the static registry cannot enumerate
  them. The runtime decides the whole kind in the primary template
  (`std::is_enum_v`); `test_enum_arm_decides_the_kind` pins that.
"""

from __future__ import annotations

import re

import pytest

from tpyc import get_runtime_dir
from tpyc import typesys as ts
from tpyc.type_def_registry import _type_defs, resolve_send_sync


INCLUDE_DIR = get_runtime_dir() / "cpp" / "include" / "tpy"
# The two rows that are about the trait MACHINERY rather than one type (the
# enum default in the primary template, the const-qualified forwarding row)
# always live with the machinery.
TYPE_TRAITS = INCLUDE_DIR / "type_traits.hpp"

# Self-check floors, deliberately well below the measured values (25 registry
# rows / 27 header rows / 63 headers as of this commit) so they never need
# touching: a scan that silently found nothing would otherwise assert nothing.
MIN_REGISTRY_ROWS = 20
MIN_HEADER_ROWS = 15
MIN_HEADER_FILES = 40

# C++ types the runtime CANNOT decide, with the reason. Each is a head name in
# the same normalized form as the two scans; the test also asserts the header
# does NOT specialize them, so a stale exception fails instead of hiding.
UNDECIDABLE: dict[str, str] = {
    # `tpy.Ptr` renders T* (through PtrType, so it has no cpp_formatter and
    # never reaches the registry scan). T* is also the borrow form the runtime
    # itself mints for a NON-value T (val_or_ptr_t<T>, val_or_ref<T>::storage_t,
    # to_val_or_ptr), so the trait cannot separate Ptr[X] from a borrow of X.
    "*": "Ptr[T] renders T*, the runtime's own borrow form for a non-value T",
}

# TypeDefs with no C++ rendering at all: `cpp_formatter` is None or emits
# `auto` (the C++ compiler deduces the iterator adapters' concrete type).
NO_CPP_RENDERING = {"tpy.FStr", "tpy.CopyIter", "tpy.OwnIter"}

# Value TypeDefs whose C++ type is a TPy-defined record: codegen emits the
# specialization next to the struct, not the header.
CODEGEN_EMITTED = {"tpy.coro.Waker"}

_FUNDAMENTAL = re.compile(r"^(bool|char|float|double|u?int(8|16|32|64)_t)$")


def _head(cpp: str) -> str:
    """Head of a C++ type: the template name, or the type itself."""
    cpp = cpp.strip()
    if cpp.endswith("*"):
        return "*"
    head = cpp.split("<", 1)[0].strip()
    return head.removeprefix("::")


def _qualify(head: str) -> str:
    """Resolve a header-side head against `namespace tpy`."""
    if "::" in head or _FUNDAMENTAL.match(head) or head == "*":
        return head
    return f"tpy::{head}"


def _canonical_args(td) -> tuple:
    """Canonical type args for a TypeDef: Int32 per TYPE parameter (str for a
    two-parameter dict view's key), 10 per INT.

    `param_kinds` is empty for a TypeDef with no registered factory
    (`tpy.varargs` is compiler-internal, so it has none) while its
    cpp_formatter still takes an element, so the arities are tried in turn
    rather than trusted."""
    kinds = td.param_kinds
    declared = tuple(10 if k is ts.TypeParamKind.INT else ts.INT32
                     for k in kinds)
    if len(kinds) == 2 and all(k is ts.TypeParamKind.TYPE for k in kinds):
        declared = (ts.STR, ts.INT32)
    for args in (declared, (ts.INT32,), (ts.STR, ts.INT32)):
        try:
            td.cpp_formatter(args)
            return args
        except IndexError:
            continue
    raise AssertionError(f"{td.qname}: cpp_formatter takes more than 2 args")


def _render(td) -> str:
    return td.cpp_formatter(_canonical_args(td))


def _registry_heads() -> dict[str, str]:
    """head -> the qname that rendered it, for every value-type TypeDef."""
    heads: dict[str, str] = {}
    for qname, td in _type_defs.items():
        if not td.is_value_type or qname in NO_CPP_RENDERING:
            continue
        if qname in CODEGEN_EMITTED or td.cpp_formatter is None:
            continue
        heads.setdefault(_head(_render(td)), qname)
    return heads


def _runtime_header_text() -> str:
    """Every runtime header, concatenated: a specialization lives next to the
    type it describes, so no single file holds the answer."""
    parts = [p.read_text() for p in sorted(INCLUDE_DIR.rglob("*.hpp"))]
    assert len(parts) >= MIN_HEADER_FILES, (
        f"only {len(parts)} headers found under {INCLUDE_DIR} -- the scan is "
        "looking in the wrong place")
    return "\n".join(parts)


def _header_heads() -> set[str]:
    pat = re.compile(r"struct\s+is_value_type<([^;]+?)>\s*:\s*std::true_type")
    return {_qualify(_head(m.group(1)))
            for m in pat.finditer(_runtime_header_text())}


def _header_false_heads(trait: str) -> set[str]:
    """Heads the runtime explicitly declares NOT Send / NOT Sync."""
    pat = re.compile(rf"struct\s+{trait}<([^;]+?)>\s*:\s*std::false_type")
    return {_qualify(_head(m.group(1)))
            for m in pat.finditer(_runtime_header_text())}


def _registry_false_heads(trait: str) -> dict[str, str]:
    """head -> qname, for every value TypeDef whose is_send / is_sync field
    resolves False at canonical args -- the rows the header must spell out
    because the trait's default follows is_value_type."""
    heads: dict[str, str] = {}
    for qname, td in _type_defs.items():
        if not td.is_value_type or qname in NO_CPP_RENDERING:
            continue
        if qname in CODEGEN_EMITTED or td.cpp_formatter is None:
            continue
        field = td.is_send if trait == "is_send" else td.is_sync
        if resolve_send_sync(field, _canonical_args(td)) is not False:
            continue
        heads.setdefault(_head(_render(td)), qname)
    return heads


def test_scans_are_not_empty():
    registry, header = _registry_heads(), _header_heads()
    assert len(registry) >= MIN_REGISTRY_ROWS, (
        f"registry scan found only {len(registry)} value-type C++ heads: "
        "the walk over _type_defs no longer sees the builtins")
    assert len(header) >= MIN_HEADER_ROWS, (
        f"header scan found only {len(header)} specializations under "
        f"{INCLUDE_DIR}: the `struct is_value_type<...>` pattern no longer "
        "matches how they are written")
    for trait in ("is_send", "is_sync"):
        # Measured 7 / 6 rows; the floor only has to prove the scan ran.
        assert len(_registry_false_heads(trait)) >= 4, (
            f"{trait} scan found almost no value TypeDef marked False -- the "
            "walk or resolve_send_sync no longer answers")


def test_every_value_type_def_has_a_runtime_specialization():
    """A value type the runtime calls a reference type gets a mutable `T&`
    slot in every generic body instantiated at it."""
    header = _header_heads()
    missing = {
        head: qname
        for head, qname in _registry_heads().items()
        if head not in header and head not in UNDECIDABLE
    }
    assert not missing, (
        "value TypeDefs whose C++ type has no tpy::is_value_type "
        f"specialization under {INCLUDE_DIR}: "
        + ", ".join(f"{q} -> {h}" for h, q in sorted(missing.items()))
    )


@pytest.mark.parametrize("head", sorted(UNDECIDABLE))
def test_undecidable_types_stay_unspecialized(head):
    """An exception that became decidable is a stale exception."""
    assert head not in _header_heads(), (
        f"{head} is listed as undecidable ({UNDECIDABLE[head]}) but a header "
        f"under {INCLUDE_DIR} now specializes it -- either remove the "
        "exception, or the specialization is wrong for the other type sharing "
        "that C++ type")


@pytest.mark.parametrize("trait", ["is_send", "is_sync"])
def test_not_send_not_sync_value_types_spell_it_out(trait):
    """`is_send` / `is_sync` inherit `is_value_type`, so a value type the
    registry calls not-Send (or not-Sync) needs its own False row -- otherwise
    making it a value type silently makes it Send."""
    header = _header_false_heads(trait)
    missing = {
        head: qname
        for head, qname in _registry_false_heads(trait).items()
        if head not in header and head not in UNDECIDABLE
    }
    assert not missing, (
        f"value TypeDefs the registry marks {trait}=False with no matching "
        f"`struct {trait}<...> : std::false_type` under {INCLUDE_DIR}: "
        + ", ".join(f"{q} -> {h}" for h, q in sorted(missing.items()))
    )


def test_const_qualified_forwarding_row_exists():
    """`is_value_type<const T>` forwards to `is_value_type<T>`. Without it a
    readonly `*args` (lowered to `varargs<const T>`) picks the opposite
    packing mode from the mutable `varargs<T>`. It ends in `is_value_type<T>`,
    not `std::true_type`, so the scan above cannot see it."""
    assert re.search(
        r"struct\s+is_value_type<const T>\s*:\s*is_value_type<T>",
        TYPE_TRAITS.read_text()), (
        f"{TYPE_TRAITS} no longer forwards const-qualified types to their "
        "unqualified value-ness")


def test_enum_arm_decides_the_kind():
    """Enums are dynamic TypeDefs (one per user/stdlib enum), so the runtime
    decides them by kind in the primary template rather than one row each."""
    text = TYPE_TRAITS.read_text()
    assert re.search(
        r"struct\s+is_value_type\s*:\s*std::bool_constant<std::is_enum_v<T>>",
        text), (
        f"{TYPE_TRAITS}'s primary is_value_type no longer answers True for a "
        "C++ enum: every generic slot at a TPy enum reverts to a mutable T&")
