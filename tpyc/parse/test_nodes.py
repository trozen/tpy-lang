"""Invariants over the AST node definitions themselves."""

import typing
from dataclasses import fields

from .. import typesys
from . import nodes as _nodes
from .nodes import TpyFieldAccess, TpyMethodCall, TpyName


def _call_slots() -> list[str]:
    """Every `TpyFieldAccess` slot that holds a dispatched method call.

    Annotations are RESOLVED rather than string-matched: the node module
    spells some of them quoted and some not, and a slot whose spelling
    differs from the one this test expected would be silently skipped --
    which is exactly the hole the test exists to close.
    """
    ns = dict(vars(_nodes))
    ns.update(vars(typesys))
    hints = typing.get_type_hints(TpyFieldAccess, ns, include_extras=False)
    return [f.name for f in fields(TpyFieldAccess)
            if _is_method_call_slot(hints.get(f.name))]


def _is_method_call_slot(hint) -> bool:
    args = typing.get_args(hint)
    return bool(args) and TpyMethodCall in args and type(None) in args


def test_hidden_call_covers_every_call_slot():
    """`hidden_call` is the ONE enumeration of the calls a field access
    dispatches to; a slot added to the node and left out of it silently
    un-vetoes whatever the aggregate feeds (duplication, reordering,
    storage-key vetoes)."""
    slots = _call_slots()
    assert slots, "no TpyMethodCall-typed slots found -- did the type spelling change?"
    for slot in slots:
        access = TpyFieldAccess(obj=TpyName(name="o"), field="f")
        call = TpyMethodCall(obj=access.obj, method="m", args=[])
        setattr(access, slot, call)
        assert access.hidden_call is call, (
            f"TpyFieldAccess.{slot} holds a method call that `hidden_call` "
            f"does not report")


def test_hidden_call_is_none_for_a_plain_field_access():
    assert TpyFieldAccess(obj=TpyName(name="o"), field="f").hidden_call is None
