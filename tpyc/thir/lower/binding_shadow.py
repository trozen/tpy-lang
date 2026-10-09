"""The binding table's shadow checks: with a collector on
`bindings.SHADOW_SINK`, a name read and `binding_of`'s record are compared
against what the lowering knows of the name apart from the table -- the
walk's `declared`, the closure locals, the read's own type, the parameter
list and the prescan. They decide nothing and raise nothing: a lowering run
with the sink set emits the same C++ as one without.
"""

from __future__ import annotations

from ...typesys import (OptionalType, TpyType, UnionType, unwrap_readonly,
                        unwrap_send_sync)
from . import bindings
from .bindings import loc_of, project_held, read_type, shadow_emit
from .predicates import (_param_is_const, _resolved_bytes_value,
                         _resolved_str_value)


def check_read(e, node, lc, declared, source) -> None:
    """A named THIRName read: the record's scope against `declared` and the
    closure locals, the stamped const against the reading function's
    parameter verdict, and the record's held under its declared type
    against the read's."""
    sink = bindings.SHADOW_SINK
    if sink is None:
        return
    name = node.name
    key, _scope = lc.cursor
    loc = loc_of(e)
    sink.append(("_stat:reads", "", "", "", ""))
    rec = lc.lookup_binding(name)
    if rec is None:
        if name in declared:
            shadow_emit(lc, "unplanned", name, loc, type(key).__name__)
        return
    if rec.nested_def:
        if name not in lc.nested_def_locals:
            shadow_emit(lc, "wrong_scope:nested_def_not_local", name, loc,
                        rec.row)
    elif name not in declared and node.cpp is None:
        shadow_emit(lc, "wrong_scope:record_not_declared", name, loc,
                    rec.row)
    pn = lc.prescan.param_names
    const = rec.const or (
        name in pn and _param_is_const(name, lc.func, lc.analyzer,
                                       lc.record_name))
    if const != source.const:
        shadow_emit(lc, f"read:const:expected={const}", name, loc, rec.row)
    if node.cpp is not None:
        return
    an = lc.analyzer
    ov = lc.prescan.owned_viewfam_params
    # A narrowed read is held as its occurrence's type, a per-read fact the
    # record does not carry; any other read is held as the record says.
    narrowed = _narrowed_read(rec.type, node.result_type)
    if _view_axis_split(rec.type, node.result_type, an):
        # The view / owned axis follows the read's type: a str / bytes
        # record whose declaration resolved the other way
        # (BUGS.md#str-rebind-after-block-local-owns) is counted, not a
        # disagreement.
        sink.append(("_stat:view_axis_split", "", "", "", ""))
        narrowed = True
    if narrowed:
        return
    for deref, label in ((False, "whole"), (True, "unwrapped")):
        mine = project_held(rec, node.result_type, deref, an, pn, ov)
        static = project_held(rec, read_type(rec.type), deref, an, pn, ov)
        if static is not mine:
            shadow_emit(lc, f"held_type:{label}:{mine.name}/{static.name}",
                        name, loc, rec.row)


def check_binding(e, rec, lc, declared) -> None:
    """`binding_of`'s record against what the parameter list and the
    prescan say of the name: the fields a conversion reads off
    `Source.binding`."""
    if bindings.SHADOW_SINK is None:
        return
    name = e.name
    loc = loc_of(e)
    param_t = next((t for n, t in lc.params if n == name), None)
    if param_t is not None and not isinstance(param_t, TpyType):
        param_t = None
    want = {"param_type": param_t,
            "is_param": name in lc.prescan.param_names}
    # The prescan names the pointer-slot GLOBALS; a local record that
    # shadows one (a comprehension variable in module init) is none.
    if rec.row.startswith("global."):
        want["global_slot"] = name in lc.prescan.global_slots
    for fld, v in want.items():
        have = getattr(rec, fld)
        same = (have == v) if fld == "param_type" \
            else bool(have) == bool(v)
        if not same:
            shadow_emit(lc, f"binding:{fld}", name, loc,
                        f"expected={v}:{type(v).__name__} "
                        f"actual={have}:{type(have).__name__} row={rec.row}")


def _view_axis_split(binding_type: TpyType | None,
                     read_t: TpyType | None, analyzer) -> bool:
    """A str / bytes binding whose declared type and read type resolve to
    different sides of the view / owned split."""
    for resolve in (_resolved_str_value, _resolved_bytes_value):
        b = resolve(read_type(binding_type), analyzer)
        r = resolve(read_t, analyzer)
        if b is not None and r is not None:
            return b != r
    return False


def _narrowed_read(binding_type: TpyType | None,
                   read_t: TpyType | None) -> bool:
    if binding_type is None or read_t is None:
        return False
    b = unwrap_readonly(unwrap_send_sync(binding_type))
    r = unwrap_readonly(unwrap_send_sync(read_t))
    return b != r and isinstance(b, (OptionalType, UnionType))


__all__ = ["check_read", "check_binding"]
